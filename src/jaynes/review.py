"""The review page (SPEC section 7): ``jaynes review`` serves the queue, stops, runs, roles and the log.

Every number on the page is a projection of the event log. The server binds
127.0.0.1 only and refuses answers that do not carry its per-process token or
that come with a foreign Host or Origin, so another site open in the browser
cannot answer a stop. Only a browser opened with the link the server prints,
which carries its key once and keeps it as a cookie, may answer: any other
client, such as an agent with curl, can read the page but not act as the
person, unless it starts a server itself and reads the link (SPEC section 7,
known limit). Each answer carries the hash of the evidence shown, so a stale
page cannot accept what came after it. After an answer the run continues in
the background.
"""

from __future__ import annotations

import errno
import html
import importlib
import json
import math
import os
import hashlib
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal, cast

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_for_filename  # pyright: ignore[reportUnknownVariableType]
from pygments.util import ClassNotFound

from jaynes import bounds, install, role_file, roles, sandbox, skill, update, verify
from jaynes import project as repo
from jaynes.conductor import (LABELS, Answer, AnswerRejected, Conductor, RunBusy, RunState, StepState, Waiting,
                              continue_in_background, new_run_id, project, return_to, start_in_background)
from jaynes.history import Report, past_calls, report
from jaynes import inputs as run_inputs
from jaynes.preview import Previews
from jaynes.policy import find_policy, load_policy
from jaynes.role import Role, Step
from jaynes.store import Event

esc = html.escape
CHIP = {"certified": "green", "refuted": "red", "floor": "blue"}  # any other status is amber
DEMO = "jaynes.demo:ROLE"
BUILT_IN = {DEMO: {"scripted": "jaynes.demo:Agent"}, **{ref: {} for ref in roles.BUILT_IN.values()}}
StageState = Literal["jaynes", "checked", "accepted", "changed", "sampled", "waiting", "blocked", "running", "todo"]

PICTURES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
            ".gif": "image/gif"}  # no SVG: it can carry scripts

def headline(task: str, most: int = 100) -> str:
    """A run's title: its task's first sentence, or as many whole words as fit in ``most`` characters. A task can be
    a whole plan, thousands of characters; the run's page keeps all of it one click away."""
    line = next((x.strip() for x in task.splitlines() if x.strip()), "")
    sentence = re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]
    if len(sentence) <= most:
        return sentence
    cut = line[:most + 1].rsplit(" ", 1)[0] if " " in line[:most + 1] else line[:most]
    return cut.rstrip(" ,;:.") + "…"


def question_parts(question: str, most: int = 120) -> tuple[str, str]:
    """A stop's question as a heading and what follows it: the first sentence, or as many whole words as fit, is
    the heading; the rest, when a step wrote a whole summary into its question, goes below in a paragraph."""
    head = headline(question, most)
    prefix = head[:-1] if head.endswith("…") and not question.startswith(head) else head
    rest = question[len(prefix):].strip(" ,;:.") if question.strip().startswith(prefix) else ""
    return head, rest.strip()


def q(segment: str) -> str:
    """One URL path segment."""
    return urllib.parse.quote(segment, safe="")


def diff_html(text: str) -> str:
    """A unified diff, escaped: added lines green, removed lines red, each file's header set apart, and the code
    highlighted in the file's language where Pygments knows it."""
    out: list[str] = []
    files = repo.diffs(text)
    for path, d in files.items() or [("", text)]:
        try:
            lexer = get_lexer_for_filename(path, stripnl=False, ensurenl=False) if path else None
        except ClassNotFound:
            lexer = None
        lines = d.rstrip("\n").split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.startswith("@@"):
                kind = "file" if line.startswith("diff --git ") else "meta" if files else "ctx"
                out.append(f'<span class="{kind}">{esc(line) or " "}</span>')
                i += 1
                continue
            out.append(f'<span class="hunk">{esc(line)}</span>')
            end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("@@")), len(lines))
            out += _hunk(lines[i + 1:end], lexer)
            i = end
    return "".join(out)


def _hunk(body: list[str], lexer: Lexer | None) -> list[str]:
    """A hunk's lines, each side read whole by the lexer so a line inside a string or comment is colored as one."""
    def side(marks: str) -> Iterator[str]:
        code = [x[1:] for x in body if x[:1] in marks]
        if lexer is None:
            return iter([esc(x) for x in code])
        formatter: HtmlFormatter[str] = HtmlFormatter(nowrap=True)
        shown = highlight("\n".join(code) + "\n", lexer, formatter).split("\n")
        return iter(shown[:len(code)])
    old, new = side(" -"), side(" +")
    out: list[str] = []
    for x in body:
        mark = x[:1]
        if mark == " ":
            next(old)
            out.append(f'<span class="ctx"><i> </i>{next(new)}</span>')
        elif mark in "+-":
            code = next(new) if mark == "+" else next(old)
            out.append(f'<span class="{"add" if mark == "+" else "del"}"><i>{mark}</i>{code}</span>')
        else:  # "\ No newline at end of file", or an empty line
            out.append(f'<span class="meta">{esc(x) or " "}</span>')
    return out


# --- reading the log -------------------------------------------------------------

@dataclass(frozen=True)
class Stage:
    name: str
    state: StageState
    detail: str
    seconds: float | None = None  # from the step's first event to its last, once it is released


def stages(names: Iterable[str], events: list[Event], st: RunState, now: float) -> list[Stage]:
    """How each step of a run was released, in role order."""
    last_answer: dict[str, str] = {}
    skipped: set[str] = set()
    span: dict[str, tuple[float, float]] = {}
    for ev in events:
        step = ev.payload.get("step")
        if step is not None:
            first = span.get(str(step), (ev.ts, ev.ts))[0]
            span[str(step)] = (first, ev.ts)
        if ev.type == "StopAnswered":
            last_answer[str(step)] = str(ev.payload["answer"])
        elif ev.type == "StopSkipped":
            skipped.add(str(step))
    out: list[Stage] = []
    for name in names:
        s = st.steps.get(name, StepState())
        took = span[name][1] - span[name][0] if name in span else None
        tries = f"{s.turns} attempt{'s' if s.turns != 1 else ''}"
        if st.blocked is not None and st.blocked["step"] == name:
            out.append(Stage(name, "blocked", f"blocked, {tries}"))
        elif s.open_stop is not None:
            again = f", round {s.round}" if s.round > 1 else ""
            out.append(Stage(name, "waiting", f"waiting {age(now - s.open_stop[0])}{again}"))
        elif s.in_flight is not None:
            out.append(Stage(name, "running", f"running, attempt {s.in_flight['attempt']}"))
        elif s.checked is not None:
            answered = last_answer.get(name) if s.resolved else None
            if s.resolved and name in skipped:
                out.append(Stage(name, "sampled", "not asked, sampled", took))
            elif answered == "accept":
                out.append(Stage(name, "accepted", "you accepted", took))
            elif answered == "change":
                out.append(Stage(name, "changed", "you changed it", took))
            elif s.checked["source"] == "jaynes":
                out.append(Stage(name, "jaynes", "by jaynes", took))
            else:
                out.append(Stage(name, "checked", f"checked, {tries}", took))
        else:
            out.append(Stage(name, "todo", ""))
    return out


@dataclass(frozen=True)
class Totals:
    turns: int
    tokens_in: int
    tokens_out: int
    seconds: float
    stops: int
    answered: int


def totals(events: list[Event]) -> Totals:
    return Totals(turns=sum(1 for ev in events if ev.type == "StepStarted"),
                  tokens_in=sum(int(ev.payload.get("tokens_in", 0)) for ev in events if ev.type == "TurnFinished"),
                  tokens_out=sum(int(ev.payload.get("tokens_out", 0)) for ev in events if ev.type == "TurnFinished"),
                  seconds=events[-1].ts - events[0].ts if events else 0.0,
                  stops=sum(1 for ev in events if ev.type == "StopOpened"),
                  answered=sum(1 for ev in events if ev.type == "StopAnswered"))


def age(seconds: float) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size:.0f}{unit}"
    return f"{max(seconds, 0):.0f}s"


def tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


# --- rendering -------------------------------------------------------------------

CSS = """
.notice { background: var(--amber-soft); color: var(--amber); padding: 10px 16px; font-size: 13px; }
.notice.new { background: var(--blue-soft); color: var(--blue); display: flex; align-items: center; gap: 12px; }
.notice.new form { margin-left: auto; }
.notice.new .btn { padding: 6px 12px; }
.qhead button { font: inherit; color: var(--blue); background: none; border: 0; padding: 0; cursor: pointer; text-transform: none; letter-spacing: 0; }
:root {
  --bg: #07090d; --panel: #0e1218; --panel-2: #121823; --line: #222a36; --line-2: #2c3646;
  --ink: #eef2f7; --dim: #93a0b2; --faint: #5d6a7c;
  --blue: #6fb4ff; --blue-soft: rgba(111, 180, 255, .12);
  --amber: #ffb35c; --amber-soft: rgba(255, 179, 92, .13);
  --green: #69dba0; --green-soft: rgba(105, 219, 160, .13);
  --red: #ff6f6f; --red-soft: rgba(255, 111, 111, .12);
  --display: "Instrument Sans", "Helvetica Neue", Arial, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--ink); font-family: var(--display); font-size: 15px; line-height: 1.5; }
a { color: inherit; text-decoration: none; }
.app { display: grid; grid-template-columns: 300px 1fr; height: 100vh; }
aside { border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; overflow: auto; }
.brand { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 10px; padding: 16px 18px; border-bottom: 1px solid var(--line); font-weight: 600; }
.brand span { font-family: var(--mono); font-weight: 400; font-size: 11px; color: var(--dim); }
.brand nav { flex-basis: 100%; display: flex; gap: 12px; font-family: var(--mono); font-size: 11px; font-weight: 400; color: var(--dim); white-space: nowrap; }
.brand nav a:hover, .crumbs a:hover { color: var(--ink); }
.qhead { padding: 14px 18px 8px; font-family: var(--mono); font-size: 11px; color: var(--dim); text-transform: uppercase; letter-spacing: .08em; display: flex; justify-content: space-between; }
.item { padding: 12px 18px; border-left: 2px solid transparent; display: grid; gap: 6px; }
.item:hover { background: rgba(255, 255, 255, .025); }
.item.on { border-left-color: var(--amber); padding-bottom: 16px; }
.item.on:hover { background: none; }
.item.on.blocked { border-left-color: var(--red); }
.item.on.idle { border-left-color: var(--blue); }
.strip { display: flex; gap: 3px; margin-top: 2px; }
.strip i { width: 14px; height: 4px; border-radius: 2px; background: var(--green); display: block; }
.strip i.waiting { background: var(--amber); }
.strip i.blocked { background: var(--red); }
.strip i.running { background: var(--blue); }
.strip i.todo { background: var(--line-2); }
.strip i.sampled { background: none; box-shadow: inset 0 0 0 1px var(--green); }
.stages { list-style: none; margin: 8px 0 0; padding: 0; }
.stages li { display: grid; grid-template-columns: 14px 1fr; gap: 10px; padding: 5px 0; position: relative; }
.stages li::before { content: ""; position: absolute; left: 6px; top: 18px; bottom: -6px; width: 1px; background: var(--line-2); }
.stages li:last-child::before { display: none; }
.stages .dot { width: 9px; height: 9px; border-radius: 50%; margin: 6px 0 0 2px; background: var(--green); position: relative; z-index: 1; }
.stages .waiting .dot { background: var(--amber); box-shadow: 0 0 0 4px var(--amber-soft); }
.stages .blocked .dot { background: var(--red); box-shadow: 0 0 0 4px var(--red-soft); }
.stages .running .dot { background: var(--blue); box-shadow: 0 0 0 4px var(--blue-soft); }
.stages .todo .dot { background: var(--bg); box-shadow: inset 0 0 0 1px var(--line-2); }
.stages .sampled .dot { background: var(--bg); box-shadow: inset 0 0 0 1.5px var(--green); }
.stages .accepted .dot, .stages .changed .dot { box-shadow: 0 0 0 3px var(--green-soft); }
.stages .r { display: flex; justify-content: space-between; gap: 8px; }
.stages b { font-family: var(--mono); font-size: 12px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stages em { font-style: normal; font-family: var(--mono); font-size: 11px; color: var(--faint); }
.stages small { font-family: var(--mono); font-size: 11px; color: var(--dim); display: block; }
.stages .todo b { color: var(--faint); font-weight: 400; }
.stages .waiting b, .stages .waiting small { color: var(--amber); }
.stages .blocked b, .stages .blocked small { color: var(--red); }
.foot { margin-top: auto; padding: 14px 18px; border-top: 1px solid var(--line); font-family: var(--mono); font-size: 11px; color: var(--dim); display: grid; grid-template-columns: 1fr auto; gap: 4px 10px; }
.foot b { color: var(--ink); font-weight: 500; text-align: right; }
.item .t { font-size: 14px; }
.item .m { font-family: var(--mono); font-size: 11px; color: var(--dim); display: flex; gap: 8px; align-items: center; white-space: nowrap; }
.item .m .s { overflow: hidden; text-overflow: ellipsis; }
.empty-q { padding: 12px 18px; font-family: var(--mono); font-size: 12px; color: var(--faint); }
.chip { font-family: var(--mono); font-size: 10.5px; padding: 2px 7px; border-radius: 4px; white-space: nowrap; }
.amber { color: var(--amber); background: var(--amber-soft); }
.red { color: var(--red); background: var(--red-soft); }
.green { color: var(--green); background: var(--green-soft); }
.blue { color: var(--blue); background: var(--blue-soft); }
main { display: flex; flex-direction: column; min-height: 0; min-width: 0; }
.crumbs { padding: 16px 32px; border-bottom: 1px solid var(--line); font-family: var(--mono); font-size: 12px; color: var(--dim); display: flex; gap: 10px; align-items: center; }
.crumbs b { color: var(--ink); font-weight: 500; }
.crumbs .r { margin-left: auto; }
.body { flex: 1; overflow: auto; padding: 28px 32px 48px; }
.body > * { max-width: 960px; }
.body > * + * { margin-top: 26px; }
.body.wide > * { max-width: none; }
h1 { font-size: 26px; font-weight: 500; letter-spacing: -0.02em; line-height: 1.2; margin: 0; text-wrap: balance; }
h2 { font-size: 18px; font-weight: 500; margin: 0; }
.sub { color: var(--dim); margin-top: 8px; }
.ask { margin: 8px 0 0; max-width: 72ch; white-space: pre-wrap; }
.label { font-family: var(--mono); font-size: 11px; color: var(--dim); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 10px; display: flex; justify-content: space-between; gap: 12px; }
.label span { text-transform: none; letter-spacing: 0; color: var(--faint); }
.box { border: 1px solid var(--line); border-radius: 6px; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-family: var(--mono); font-size: 10.5px; font-weight: 400; color: var(--faint); padding: 9px 16px; }
td { border-top: 1px solid var(--line); padding: 12px 16px; vertical-align: top; }
td.m, td .m, .m { font-family: var(--mono); font-size: 11.5px; color: var(--dim); }
td.id { font-family: var(--mono); font-size: 12px; color: var(--blue); white-space: nowrap; }
.kv td:first-child { font-family: var(--mono); font-size: 11.5px; color: var(--dim); width: 160px; }
ul.plain { margin: 0; padding-left: 18px; }
pre { margin: 0; font-family: var(--mono); font-size: 12px; white-space: pre-wrap; word-break: break-word; color: var(--dim); }
.diff > span { display: block; padding: 0 16px; }
.diff > span > i { font-style: normal; display: inline-block; width: 2ch; color: var(--faint); user-select: none; }
.diff > .add, .diff > .del { color: var(--ink); }
.diff > .add { background: var(--green-soft); }
.diff > .add > i { color: var(--green); }
.diff > .del { background: var(--red-soft); }
.diff > .del > i { color: var(--red); }
.diff > .hunk { color: var(--blue); }
.diff > .file { color: var(--ink); font-weight: 600; margin-top: 10px; }
.diff > .file:first-child { margin-top: 0; }
.diff > .meta { color: var(--faint); }
.diff .k, .diff .kd, .diff .kn, .diff .kr, .diff .kc, .diff .kp, .diff .ow, .diff .na { color: #c4a7ff; }
.diff .kt, .diff .nb, .diff .bp, .diff .nd { color: #ff9e64; }
.diff .s, .diff .s1, .diff .s2, .diff .sa, .diff .sb, .diff .sc, .diff .sd, .diff .se, .diff .sh, .diff .si,
.diff .sr, .diff .ss, .diff .sx, .diff .dl { color: #f2c48d; }
.diff .c, .diff .c1, .diff .cm, .diff .cp, .diff .cs, .diff .ch, .diff .cpf { color: #6b7789; font-style: italic; }
.diff .m, .diff .mi, .diff .mf, .diff .mh, .diff .mo, .diff .mb, .diff .il { color: #7cc4ff; }
.diff .nf, .diff .fm, .diff .nc, .diff .nn, .diff .ne { color: #6fe0d0; }
.diff .nt { color: #ff9fc0; }
.diff .gh, .diff .gu { color: var(--blue); font-weight: 600; }
.field + .field { margin-top: 18px; }
.checks { display: flex; gap: 8px; flex-wrap: wrap; }
.tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: var(--line); border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.tiles div { background: var(--panel); padding: 14px 16px; }
.tiles .n { font-size: 22px; font-weight: 500; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.tiles .n small { font-size: 14px; color: var(--dim); font-weight: 400; margin-left: 4px; letter-spacing: 0; }
.tiles p { margin: 2px 0 0; font-family: var(--mono); font-size: 11px; color: var(--dim); }
.bar { border-top: 1px solid var(--line); background: rgba(7, 9, 13, .96); padding: 14px 32px; display: grid; gap: 10px; }
.bar .row { display: flex; gap: 10px; align-items: center; }
.bar input[type=text] { flex: 1; min-width: 0; background: var(--panel); border: 1px solid var(--line-2); border-radius: 4px; color: var(--ink); font: inherit; font-size: 14px; padding: 10px 12px; }
.bar select { background: var(--panel); border: 1px solid var(--line-2); border-radius: 4px; color: var(--ink); font-family: var(--mono); font-size: 12.5px; padding: 10px 8px; }
.bar textarea { width: 100%; min-height: 160px; background: var(--panel); border: 1px solid var(--line-2); border-radius: 4px; color: var(--ink); font-family: var(--mono); font-size: 12.5px; padding: 10px 12px; }
.btn { font-family: var(--mono); font-size: 12.5px; padding: 10px 14px; border-radius: 4px; border: 1px solid var(--line-2); color: var(--ink); background: var(--panel); cursor: pointer; white-space: nowrap; }
details.ran summary { cursor: pointer; }
details.ran pre { margin: 8px 0 0; max-height: 320px; overflow: auto; white-space: pre-wrap; color: var(--dim); }
.btn kbd { font-family: var(--mono); font-size: 10px; color: var(--faint); margin-left: 6px; }
.btn.solid { background: var(--green); border-color: var(--green); color: #04150c; font-weight: 500; }
.btn.solid kbd { color: #0b3a22; }
.err { color: var(--red); font-family: var(--mono); font-size: 12px; white-space: pre-line; }
.hidden { display: none; }
.empty { margin: auto; text-align: center; color: var(--dim); display: grid; gap: 6px; }
.empty h1 { color: var(--ink); }
.filters { display: flex; gap: 8px; flex-wrap: wrap; font-family: var(--mono); font-size: 11px; }
.filters a { padding: 3px 8px; border: 1px solid var(--line-2); border-radius: 999px; color: var(--dim); }
.filters a.on { color: var(--ink); border-color: var(--blue); }
.form { display: grid; gap: 20px; max-width: 640px; }
.form label { display: grid; }
.form select, .form input[type=text], .form textarea { background: var(--panel); border: 1px solid var(--line-2); border-radius: 4px; color: var(--ink); font: inherit; font-size: 14px; padding: 10px 12px; }
.form textarea { min-height: 96px; resize: vertical; }
.sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 7px; vertical-align: -1px; box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .18); }
.previews { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.previews figure { margin: 0; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; display: grid; grid-template-rows: auto 1fr; }
.previews figcaption { display: flex; justify-content: space-between; padding: 8px 12px; border-bottom: 1px solid var(--line); font-family: var(--mono); font-size: 11px; color: var(--dim); }
.previews iframe { width: 100%; height: 520px; border: 0; background: #fff; }
.body > .wide { max-width: none; }
.pictures { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
.pictures a { border: 1px solid var(--line); border-radius: 6px; overflow: hidden; display: grid; }
.pictures img { width: 100%; height: 220px; object-fit: cover; object-position: top; display: block; background: var(--panel); }
.pictures span { padding: 8px 12px; border-top: 1px solid var(--line); font-family: var(--mono); font-size: 11px; color: var(--dim); }
.btn.go { background: var(--blue); border-color: var(--blue); color: #04101f; font-weight: 500; justify-self: start; }
details.others { border-top: 1px solid var(--line); margin-top: 8px; }
details.others > summary { cursor: pointer; list-style: none; }
details.others > summary::-webkit-details-marker { display: none; }
details.others > summary span:first-child::before { content: "\\25B8  "; }
details.others[open] > summary span:first-child::before { content: "\\25BE  "; }
.cards { display: grid; gap: 12px; }
.card { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); overflow: hidden; }
.card .pics { display: flex; flex-wrap: wrap; gap: 10px; padding: 10px 10px 0; }
.card .shot { position: relative; display: block; cursor: zoom-in; }
.card .shot.wide { flex: 1 1 100%; }
.card .shot.tall { flex: 1 1 calc(50% - 5px); }
.card .shot .cut { overflow: hidden; border-radius: 4px; background: var(--bg); box-shadow: 0 0 0 1px var(--line); }
.card .shot img { display: block; max-width: none; }
.card .shot > img { width: 100%; }
.card .shot span { display: block; margin-bottom: 4px; font-family: var(--mono); font-size: 11px; color: var(--dim); }
#zoom { position: fixed; inset: 0; z-index: 10; background: rgba(4, 6, 9, .94); overflow: auto; cursor: zoom-out; display: grid; place-items: start center; padding: 48px 24px; }
#zoom.hidden { display: none; }
#zoom img { max-width: none; box-shadow: 0 0 0 1px var(--line-2); }
#zoom p { position: fixed; top: 12px; right: 18px; margin: 0; font-family: var(--mono); font-size: 12px; color: var(--dim); }
.card .txt { display: flex; gap: 12px; align-items: flex-start; padding: 12px 14px; }
.card .txt b { font-weight: 500; }
.card .txt p { margin: 2px 0 0; color: var(--dim); }
.card.pick { cursor: pointer; }
.card.pick:hover { border-color: var(--line-2); }
.card.pick:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
.card.pick i.n { flex: none; width: 24px; height: 24px; border-radius: 50%; box-shadow: inset 0 0 0 1px var(--line-2); font-style: normal; font-family: var(--mono); font-size: 12px; display: grid; place-items: center; margin-top: 1px; }
.card.pick.on { border-color: var(--green); }
.card.pick.on i.n { background: var(--green); color: #04150c; box-shadow: none; }
details.steps > summary { cursor: pointer; font-family: var(--mono); font-size: 11px; color: var(--dim); margin-top: 4px; }
details.steps .foot { margin: 10px -18px -16px; }
.item.on > a { display: grid; gap: 6px; }
.item.on .m { flex-wrap: wrap; white-space: normal; }
details.fold { border-top: 1px solid var(--line); padding-top: 14px; }
details.fold > summary { cursor: pointer; font-family: var(--mono); font-size: 12px; color: var(--dim); }
details.fold > summary span { color: var(--faint); margin-left: 6px; }
details.fold .task { margin: 10px 0 14px; white-space: pre-wrap; color: var(--dim); max-width: 72ch; }
details.fold[open] > summary { margin-bottom: 18px; }
details.fold > * + * { margin-top: 22px; }
.bar .row { flex-wrap: wrap; }
.bar .acts { display: flex; gap: 10px; align-items: center; }
details.menu { position: relative; }
details.menu > summary { list-style: none; }
details.menu > summary::-webkit-details-marker { display: none; }
details.menu .pop { position: absolute; bottom: calc(100% + 8px); right: 0; z-index: 2; display: grid; gap: 8px; padding: 12px; min-width: 280px; background: var(--panel-2); border: 1px solid var(--line-2); border-radius: 6px; box-shadow: 0 12px 32px rgba(0, 0, 0, .5); }
details.menu .pop label { display: grid; gap: 6px; font-family: var(--mono); font-size: 11px; color: var(--dim); }
details.menu .pop .btn { text-align: left; }
.call { display: flex; align-items: center; gap: 14px; padding: 14px 16px; border-radius: 6px; background: var(--amber-soft); color: var(--amber); }
.call span { flex: 1; }
.call.blocked { background: var(--red-soft); color: var(--red); }
.call.idle { background: var(--blue-soft); color: var(--blue); }
@media (max-width: 760px) {
  .app { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
  aside { border-right: 0; border-bottom: 1px solid var(--line); overflow: visible; }
  aside > :not(.brand) { display: none; }
  .brand { padding: 10px 16px; }
  .crumbs { padding: 10px 16px; font-size: 11px; white-space: nowrap; overflow: hidden; }
  .crumbs > * { min-width: 0; overflow: hidden; text-overflow: ellipsis; flex-shrink: 1; }
  .crumbs b { flex-shrink: 0; }
  .crumbs .r { display: none; }
  .body { padding: 18px 16px 32px; }
  h1 { font-size: 21px; }
  .bar { padding: 10px 16px; }
  .bar .row > input[type=text] { flex-basis: 100%; }
  .bar .acts { margin-left: auto; }
  .tiles { grid-template-columns: 1fr; }
  .previews { grid-template-columns: 1fr; }
  .card .shot.tall { flex-basis: 100%; }  /* a phone's picture at about its own size, one to a row */
}
"""

ICON = ('<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 32 32%22%3E%3Crect width=%2232%22 height=%2232%22 rx=%227%22 fill=%22%2307090d%22/%3E%3Cpath d=%22M11.5 14.6 20 9.6M11.5 17.4 20 22.4%22 stroke=%22%236fb4ff%22 stroke-width=%222%22/%3E%3Ccircle cx=%229%22 cy=%2216%22 r=%224.2%22 fill=%22%236fb4ff%22/%3E%3Ccircle cx=%2222.5%22 cy=%228.5%22 r=%223.2%22 fill=%22%23eef2f7%22/%3E%3Ccircle cx=%2222.5%22 cy=%2223.5%22 r=%223.2%22 fill=%22%23eef2f7%22/%3E%3C/svg%3E">')

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600'
         '&family=IBM+Plex+Mono:wght@400;500&display=swap">')

# Reload when the log grows, unless the person is typing; keys answer the open stop. A floor, which never
# passes without a person, accepts on the second A within two seconds, so a stray key does not release it.
SCRIPT = """
<script>
const seq = %d, boot = document.body.dataset.boot;
const posted = ["/answer", "/continue", "/dismiss", "/send-back", "/start", "/setup", "/update", "/restart"];  // no page to GET
const busy = () => { const f = document.activeElement; return f && ["INPUT", "TEXTAREA", "SELECT"].includes(f.tagName); };
let poll = null;
const check = async () => {
  try {
    const [now, at] = (await (await fetch("/seq")).text()).split(" ");
    if (at !== boot) {  // Jaynes restarted and kept this browser's key: this tab moves to it
      clearInterval(poll);
      location.replace(posted.includes(location.pathname) ? "/" : location.href);
      return;
    }
    if (Number(now) !== seq && !busy() && (document.querySelector("#editor") || {}).className !== "") location.reload();
  } catch (e) {}
};
check(); poll = setInterval(check, 2500);
addEventListener("pagehide", () => navigator.sendBeacon("/bye"));
// A stop that appeared since this tab last loaded: the count in the title, and a notification if the person
// is elsewhere. Without a tab open, the server opens one instead.
const qhead = document.querySelector(".qhead"), waiting = qhead ? Number(qhead.dataset.waiting) : 0;
if (waiting) document.title = "(" + waiting + ") " + document.title;
let before = null;
try { before = sessionStorage.getItem("jaynes-waiting"); sessionStorage.setItem("jaynes-waiting", waiting); } catch (e) {}
const first = document.querySelector("a.item.stop, a.item.blocked, a.item.idle");
if ("Notification" in window && Notification.permission === "granted" && before !== null && waiting > Number(before)
    && !document.hasFocus() && first) {
  const n = new Notification("Jaynes: your answer", {body: first.querySelector(".t").textContent, tag: "jaynes"});
  n.onclick = () => { window.focus(); n.close(); location.href = first.getAttribute("href"); };
}
const ask = document.querySelector("#notify");
if (ask && "Notification" in window && Notification.permission === "default") {
  ask.classList.remove("hidden");
  ask.addEventListener("click", () => Notification.requestPermission().then(() => ask.classList.add("hidden")));
}
// a picture enlarges here, whole and at its own size; Escape or a click closes it
const zoom = document.createElement("div");
zoom.id = "zoom"; zoom.className = "hidden"; zoom.innerHTML = "<p>Esc closes</p><img alt=''>";
document.body.appendChild(zoom);
const unzoom = () => { zoom.className = "hidden"; zoom.querySelector("img").removeAttribute("src"); };
zoom.addEventListener("click", unzoom);
document.querySelectorAll("[data-zoom]").forEach(a => a.addEventListener("click", (ev) => {
  ev.preventDefault(); ev.stopPropagation();
  zoom.querySelector("img").src = a.dataset.zoom; zoom.className = ""; zoom.scrollTo(0, 0);
}));
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && zoom.className !== "hidden") { ev.preventDefault(); ev.stopImmediatePropagation(); unzoom(); }
}, true);
const form = document.querySelector("#answer");
if (form) {
  const note = form.querySelector("[name=note]"), hint = form.querySelector("#hint"), editor = form.querySelector("#editor");
  const chosen = form.querySelector("[name=chosen]"), accept = form.querySelector("[data-a=accept]");
  const picked = () => chosen.value.split(" ").filter(Boolean);
  const show = () => {
    const ids = picked();
    document.querySelectorAll(".card.pick").forEach(c => {
      const at = ids.indexOf(c.dataset.id);
      c.classList.toggle("on", at >= 0);
      c.querySelector("i.n").textContent = at >= 0 ? at + 1 : "";
    });
    accept.firstChild.textContent = ids.length ? "Choose " + ids.length + ", in this order" : "Accept";
  };
  document.querySelectorAll(".card.pick").forEach(c => {
    const toggle = () => {
      const ids = picked(), at = ids.indexOf(c.dataset.id);
      if (at >= 0) ids.splice(at, 1); else ids.push(c.dataset.id);
      chosen.value = ids.join(" "); show();
    };
    c.addEventListener("click", (ev) => { if (!ev.target.closest("a")) toggle(); });
    c.addEventListener("keydown", (ev) => { if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); toggle(); } });
  });
  if (chosen.value) show();
  const send = (a) => {
    if (!form.querySelector("[data-a=" + a + "]")) return;
    if (a === "accept" && chosen.value.trim()) a = "choose";
    if ((a === "return" || a === "context") && !note.value.trim()) { hint.textContent = a + " needs a note for the agent"; note.focus(); return; }
    if (a === "change" && editor.className === "hidden") { editor.className = ""; editor.querySelector("textarea").focus(); return; }
    form.querySelector("[name=answer]").value = a; form.submit();
  };
  form.querySelectorAll("[data-a]").forEach(b => b.addEventListener("click", () => send(b.dataset.a)));
  let armed = null;
  document.addEventListener("keydown", (ev) => {
    if (busy() || ev.metaKey || ev.ctrlKey || ev.altKey || zoom.className !== "hidden") return;
    const a = {a: "accept", c: "change", r: "return", x: "context"}[ev.key.toLowerCase()];
    if (!a) return;
    ev.preventDefault();
    if (a === "accept" && form.dataset.floor && !armed) {
      const label = accept.firstChild.textContent;
      accept.firstChild.textContent = "Press A again to " + (chosen.value.trim() ? "choose" : "accept");
      armed = setTimeout(() => { accept.firstChild.textContent = label; armed = null; }, 2000);
      return;
    }
    send(a);
  });
}
</script>
"""


def page(title: str, sidebar: str, main: str, seq: int) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(title)}</title>{ICON}'
            f'{FONTS}<style>{CSS}</style></head><body><div class="app">{sidebar}<main>{main}</main></div>'
            f'{SCRIPT % seq}</body></html>')


def render_value(v: Any) -> str:
    """Generic, readable rendering of a step output: lists of records become tables."""
    if isinstance(v, list) and v and all(isinstance(x, dict) for x in cast(list[Any], v)):
        rows = cast(list[dict[str, Any]], v)
        keys = list(dict.fromkeys(k for r in rows for k in r))
        head = "".join(f"<th>{esc(k)}</th>" for k in keys)
        body = "".join("<tr>" + "".join(_cell(r.get(k), i == 0) for i, k in enumerate(keys)) + "</tr>" for r in rows)
        return f'<div class="box"><table><tr>{head}</tr>{body}</table></div>'
    if isinstance(v, list):
        items = cast(list[Any], v)
        return '<ul class="plain">' + "".join(f"<li>{render_value(x)}</li>" for x in items) + "</ul>"
    if isinstance(v, dict):
        fields = cast(dict[str, Any], v)
        if all(not isinstance(x, (dict, list)) for x in fields.values()):
            return '<div class="box"><table class="kv">' + "".join(
                f"<tr><td>{esc(k)}</td><td>{render_value(x)}</td></tr>" for k, x in fields.items()) + "</table></div>"
        return "".join(f'<div class="field"><div class="label">{esc(k)}</div>{render_value(x)}</div>' for k, x in fields.items())
    if v is None:
        return '<span class="m">none</span>'
    return swatch(v)


HEX = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?(?:[0-9a-fA-F]{2})?")


def swatch(v: Any) -> str:
    """A scalar as text, with a colour sample when it is a hex colour."""
    text = str(v)
    return (f'<span class="sw" style="background:{text}"></span>{esc(text)}' if isinstance(v, str) and HEX.fullmatch(text)
            else esc(text))


def _cell(v: Any, first: bool) -> str:
    if first and isinstance(v, (str, int)) and len(str(v)) <= 8:
        return f'<td class="id">{esc(str(v))}</td>'
    if isinstance(v, (dict, list)):
        return f"<td><pre>{esc(json.dumps(v, indent=1, ensure_ascii=False))}</pre></td>"
    if isinstance(v, (int, float)) or (isinstance(v, str) and len(v) <= 24):
        return f'<td class="m">{swatch(v)}</td>'
    return f"<td>{render_value(v)}</td>"


def _size(picture: Path) -> tuple[int, int] | None:
    """A picture's width and height, read from a PNG's header; None for anything else."""
    try:
        with picture.open("rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    return (width, height) if width and height else None


def stage_list(items: list[Stage]) -> str:
    """The open queue item's stages, top to bottom, each with how it was released and how long it took."""
    rows = "".join(
        f'<li class="{s.state}"><span class="dot"></span><div><div class="r"><b>{esc(s.name)}</b>'
        f'<em>{"" if s.seconds is None else "under 1s" if s.seconds < 1 else age(s.seconds)}</em></div>'
        + (f"<small>{esc(s.detail)}</small>" if s.detail else "") + "</div></li>"
        for s in items)
    return f'<ul class="stages">{rows}</ul>'


def stage_strip(items: list[Stage]) -> str:
    """One mark per stage, for queue items that are not open."""
    return '<div class="strip">' + "".join(
        f'<i class="{s.state}" title="{esc(s.name)}"></i>' for s in items) + "</div>"


# --- the application ---------------------------------------------------------------

@dataclass
class Shared:
    """What outlives a request: the answer token and key, imported roles, and when each open stop was first shown."""

    token: str
    key: str
    idle_after: float = 10.0
    roles: dict[str, Role] = field(default_factory=dict[str, Role])
    first_seen: dict[tuple[str, str, float], float] = field(default_factory=dict[tuple[str, str, float], float])
    history: tuple[float, Report | None, str] | None = None  # when it was read, the report, the policy used
    previews: Previews = field(default_factory=Previews)
    polled: float = 0.0  # when a tab opened as the person last asked for /seq: one is open
    latest: str | None = None  # the commit Jaynes's address points at, read in the background


class Review:
    """One request's view of the log. SQLite connections stay in their thread, so each request makes one."""

    def __init__(self, home: Path, shared: Shared) -> None:
        self.c = Conductor(home)
        self.shared = shared
        self._queue: list[Waiting] | None = None

    def seq(self) -> int:
        return self.c.store.max_run_seq()

    def queue(self) -> list[Waiting]:
        """Read once per request: the sidebar and the page show the same queue."""
        if self._queue is None:
            self._queue = self.c.queue(idle_after=self.shared.idle_after)
        return self._queue

    def role_of(self, run_id: str) -> Role | None:
        """The role a run was started with, if its module can be imported here. Edits need a restart."""
        started = next(iter(self.c.store.events("RunStarted", run_id=run_id)), None)
        return self.import_role(str(started.payload.get("ref", "")) if started else "")

    def import_role(self, ref: str) -> Role | None:
        if ref not in self.shared.roles:
            module, _, attr = ref.partition(":")
            try:
                if ref.endswith(".toml"):
                    found = role_file.load(Path(ref))
                else:
                    found = getattr(importlib.import_module(module), attr) if module and attr else None
            except Exception:
                return None  # not cached, so a fixed import works without a restart
            if not isinstance(found, Role):
                return None
            self.shared.roles[ref] = found
        return self.shared.roles[ref]

    def names(self, role: Role | None, events: list[Event]) -> list[str]:
        """The run's steps in role order, or in the order they appear when the role cannot be imported."""
        if role is not None:
            return [s.name for s in role.steps]
        return list(dict.fromkeys(str(ev.payload["step"]) for ev in events if "step" in ev.payload))

    # sidebar ----------------------------------------------------------------

    def sidebar(self, selected: tuple[str, str] | None) -> str:
        """What waits for the person, the open stops; below it, folded, runs that are blocked, idle or working."""
        items = self.queue()
        now = self.c.clock()
        starts = {w.run_id: next(iter(self.c.store.events("RunStarted", run_id=w.run_id))).ts for w in items}
        tasks = [w.task for w in items]
        waiting: list[str] = []
        others: list[str] = []
        unfold = False
        for w in items:
            on = selected == (w.run_id, w.step) or selected == (w.run_id, "")
            events = list(self.c.store.events(run_id=w.run_id))
            names = self.names(self.role_of(w.run_id), events)
            run_stages = stages(names, events, project(events), now)
            chip = {"stop": '<span class="chip amber">stop</span>', "blocked": '<span class="chip red">blocked</span>',
                    "idle": '<span class="chip blue">idle</span>', "running": '<span class="chip blue">running</span>',
                    "paused": '<span class="chip amber">paused</span>'}[w.kind]
            # a run that needs nobody opens its run page; it continues by itself
            href = f"/stop/{q(w.run_id)}/{q(w.step)}" if w.needs_person else f"/run/{q(w.run_id)}"
            when = (f"until {time.strftime('%H:%M', time.localtime(w.until))}" if w.until is not None
                    else age(now - w.since))
            started = ""
            if tasks.count(w.task) > 1:  # the same task twice: told apart by when each started
                at = time.localtime(starts[w.run_id])
                same_day = time.strftime("%Y%m%d", at) == time.strftime("%Y%m%d", time.localtime(now))
                started = f'<span>started {time.strftime("%H:%M" if same_day else "%b %d %H:%M", at)}</span>'
            if on:  # where the run is, in a line; its steps and their cost a click away
                where = names.index(w.step) + 1 if w.step in names else 0
                state = {"stop": "waiting for you", "blocked": "blocked", "idle": "not running",
                         "running": "working", "paused": "paused for quota"}[w.kind]
                t = totals(events)
                row = (f'<div class="item on {w.kind}"><a href="{href}"><div class="t">{esc(headline(w.task))}</div>'
                       f'<div class="m">{chip}<span class="s">Step {where} of {len(names)}, {state}</span>{started}'
                       f'</div></a><details class="steps"><summary>Steps</summary>{stage_list(run_stages)}'
                       f'<div class="foot"><span>started</span><b>{age(now - events[0].ts)} ago</b>'
                       f'<span>turns</span><b>{t.turns}</b>'
                       f'<span>tokens</span><b>{tokens(t.tokens_in)} in, {tokens(t.tokens_out)} out</b>'
                       f'<span>you answered</span><b>{t.answered} of {t.stops} stops</b></div></details></div>')
            else:
                row = (f'<a class="item {w.kind}" href="{href}"><div class="t">{esc(headline(w.task))}</div>'
                       f'<div class="m">{chip}<span class="s">{esc(w.step)}</span>{started}<span>{when}</span></div>'
                       f'{stage_strip(run_stages)}</a>')
            (waiting if w.kind == "stop" else others).append(row)
            unfold = unfold or (on and w.kind != "stop")
        body = "".join(waiting) or '<div class="empty-q">Nothing is waiting for you.</div>'
        if others:
            body += (f'<details class="others"{" open" if unfold else ""}><summary class="qhead"><span>Other runs</span>'
                     f'<span>{len(others)}</span></summary>{"".join(others)}</details>')
        return ('<aside><div class="brand"><a href="/">Jaynes</a> <span>review</span><nav>'
                '<a href="/">Queue</a><a href="/new">New run</a><a href="/roles">Roles</a><a href="/history">History</a><a href="/log">Log</a><a href="/setup">Setup</a></nav></div>'
                f'<div class="qhead" data-waiting="{len(waiting)}"><span>Waiting for you</span>'
                '<button id="notify" class="hidden" type="button">Notify me</button>'
                f'<span>{len(waiting)}</span></div>'
                f'{body}</aside>')

    # pages ------------------------------------------------------------------

    def queue_page(self) -> str:
        items = [w for w in self.queue() if w.kind == "stop"]
        if items:
            return self.stop_page(items[0].run_id, items[0].step)
        main = ('<div class="crumbs"><b>Queue</b></div><div class="body" style="display:flex">'
                '<div class="empty"><h1>Nothing is waiting</h1><p>Runs that stop or block will appear here.</p>'
                '<p><a class="btn go" href="/new">Start a run</a></p></div></div>')
        return page("Jaynes review", self.sidebar(None), main, self.seq())

    def stop_page(self, run_id: str, step: str, error: str = "", fields: dict[str, str] | None = None) -> str:
        w = next((x for x in self.queue() if (x.run_id, x.step) == (run_id, step)), None)
        if w is None or not w.needs_person:  # gone, running or paused: nothing to answer
            return self.run_page(run_id)
        events = list(self.c.store.events(run_id=run_id))
        st = project(events)
        role = self.role_of(run_id)
        target = next((s for s in role.steps if s.name == step), None) if role else None
        s = st.steps.get(step, StepState())
        crumbs = (f'<div class="crumbs"><span>{esc(w.role)}</span>/<a href="/run/{q(run_id)}">{esc(headline(w.task))}</a>/'
                  f'<b>{esc(step)}</b><span class="r">{esc(self._where(s))}</span></div>')
        parts: list[str] = []
        if w.kind == "idle":
            log = self.c.home / "runs" / run_id / "continue.log"
            tail = log.read_text(errors="replace")[-1500:] if log.exists() else ""
            parts.append(f"<div><h1>This run is not running</h1>"
                         f'<div class="sub">No process holds it and it is not waiting for you; its last process '
                         f"stopped or failed. Continue starts <code>jaynes run</code> for it.</div></div>")
            if tail:
                parts.append(f'<div><div class="label">Last output</div><div class="box"><pre style="padding:12px 16px">'
                             f"{esc(tail)}</pre></div></div>")
            main = crumbs + f'<div class="body">{"".join(parts)}</div>' + self._run_bar(run_id, cont=True)
            return page(f"Idle: {run_id}", self.sidebar((run_id, step)), main, self.seq())
        if w.kind == "blocked" and (st.blocked or {}).get("at") == "branch":
            parts.append("<div><h1>This run is blocked after its last step</h1>"
                         '<div class="sub">Every step finished, but its changes did not reach your repository:</div>'
                         "</div>")
        elif w.kind == "blocked":
            parts.append(f"<div><h1>This run is blocked at {esc(step)}</h1>"
                         f'<div class="sub">Every attempt of the round was rejected. The last reasons:</div></div>')
        if w.kind == "blocked":
            parts.append('<div class="box"><table>' + "".join(
                f'<tr><td class="m" style="color:var(--red)">rejected</td><td>{esc(r)}</td></tr>' for r in w.reasons)
                + "</table></div>")
            parts.append(self._turns(events, step, s.round))
            at_branch = (st.blocked or {}).get("at") == "branch"
            bar = (self._run_bar(run_id, cont=False) if at_branch or role is None
                   else self._send_back_bar(run_id, role, st, step))
            main = crumbs + f'<div class="body">{"".join(parts)}</div>' + bar
            return page(f"Blocked: {step}", self.sidebar((run_id, step)), main, self.seq())

        value = self._value(s, target)
        pure = target is not None and target.output is None  # a stop over what earlier steps made
        question = self._question_of(w, role)
        rejected = [ev for ev in events if ev.type == "TurnRejected" and ev.payload["step"] == step
                    and self._round_of(events, ev) == s.round]
        sub = (f'<div class="sub">{len(rejected)} earlier attempt{"s" if len(rejected) != 1 else ""} '
               f'rejected by Jaynes before you saw it: {esc(rejected[-1].payload["reasons"][0])}</div>'
               if rejected and rejected[-1].payload.get("reasons") else "")
        if s.open_stop is not None and s.open_stop[1].get("ends"):
            sub += ('<div class="sub">Accepting ends the run here, with nothing delivered; return it with a note '
                    'to have the agent look again.</div>')
        if s.note:
            sub += f'<div class="sub">You returned round {s.round - 1} with: {esc(s.note)}</div>'
        shown = cast(list[dict[str, Any]] | None, s.open_stop[1].get("shown")) if s.open_stop else None
        items = self._items(target, s)
        if items:
            sub += ('<div class="sub">Click them in order of importance to choose; Accept keeps them all, in the order '
                    'shown.</div>')
        head, rest = question_parts(question)
        parts.append(f"<div><h1>{esc(head)}</h1>" + (f'<p class="ask">{esc(rest)}</p>' if rest else "") + f"{sub}</div>")
        parts.append(self._floor_part(s))  # code that runs later, outside any box, is always in front
        # in front, what the person judges; behind Details, the output, its checks, turns and statistics
        details: list[str] = [self._project_part(run_id, st, role), self._pictures_part(run_id, st, target)]
        outputs: list[str] = []
        if pure and target is not None:
            for need in target.needs:
                outputs.append(self._output(f"From {need.name}", self._value(st.steps.get(need.name, StepState()), need)))
        else:
            outputs.append(self._output("Output", value, f"round {s.round}"))
            if s.previous:
                outputs.append(self._output("Before", self._value_of(s.previous, target), f"round {s.round - 1}, returned"))
        if shown:
            parts.append(self._shown(run_id, st, [x for x in shown if not x.get("more")], items))
            more = [x for x in shown if x.get("more")]
            if more:
                parts.append(f'<details class="fold"><summary>More <span>{len(more)}</span></summary>'
                             f'{self._shown(run_id, st, more, [])}</details>')
            details = outputs + details
        else:
            if items:
                parts.append(self._shown(run_id, st, [{"title": label, "id": i} for i, label in items], items))
            parts += details + outputs
            details = []
        if target is not None and target.checks:
            # a Checked witness means every check passed; the names come from the role's code
            check_names = ", ".join(esc(getattr(c, "__name__", "check")) for c in target.checks)
            details.append(f'<div><div class="label">Checks</div><div class="checks">'
                           f'<span class="chip green">passed: {check_names}</span></div></div>')
        if s.open_stop is not None:
            self.shared.first_seen.setdefault((run_id, step, s.open_stop[0]), time.time())
        if not pure:
            details.append(self._turns(events, step, s.round))
        details.append(self._tiles(s, target))
        parts.append(f'<details class="fold"><summary>Details <span>the output, its checks and turns</span></summary>'
                     f'{"".join(details)}</details>')
        main = (crumbs + f'<div class="body">{"".join(parts)}</div>'
                + self._answer_bar(run_id, step, value, error, fields, editable=not pure,
                                   earlier=self._earlier(role, step),
                                   default_to=return_to(role, step) if pure and role else None,
                                   floor=bool(s.open_stop and s.open_stop[1].get("status") == "floor"),
                                   items=items,
                                   evidence=str(s.open_stop[1].get("evidence") or "") if s.open_stop else ""))
        return page(f"Stop: {step}", self.sidebar((run_id, step)), main, self.seq())

    def _project_part(self, run_id: str, st: RunState, role: Role | None) -> str:
        """What the run changed in its project so far, and the preview before and after, when there is one."""
        info = (st.started or {}).get("project")
        changed = sorted({f for x in st.steps.values() if x.checked for f in x.checked.get("changed", [])})
        if not info or not changed:
            return ""
        out = ""
        if (st.started or {}).get("preview") and role is not None:
            frames = "".join(
                f'<figure><figcaption><span>{label}</span><a href="/preview/{q(run_id)}/{which}" target="_blank">'
                f'open</a></figcaption><iframe src="/preview/{q(run_id)}/{which}" loading="lazy"></iframe></figure>'
                for label, which in (("Before", "before"), ("After", "after")))
            out += f'<div class="wide"><div class="label">Preview <span>your preview command, on copies of the project</span></div><div class="previews">{frames}</div></div>'
        files = "".join(f'<tr><td class="m">{esc(f)}</td></tr>' for f in changed)
        out += (f'<div><div class="label">Changed in the project <span>{esc(str(info["root"]))}, from '
                f'{esc(str(info["base"])[:8])}</span></div><div class="box"><table>{files}</table></div></div>')
        for step, patch in self.c.patches(role, st) if role is not None else []:
            text = patch.decode(errors="replace")
            if len(text) > 20000:  # never cut silently: what is not shown here must still be read
                text = text[:20000] + f"\n\n[{len(text) - 20000} more characters not shown: read the branch before merging it]"
            out += (f'<div class="wide"><div class="label">Changes from {esc(step)}</div><div class="box">'
                    f'<pre class="diff" style="padding:12px 0">{diff_html(text)}</pre></div></div>')
        return out

    def _floor_part(self, s: StepState) -> str:
        """The diff of the paths this step changed that tools run later, which is why the stop is a floor."""
        later = set((s.checked or {}).get("floor") or [])
        patch = (s.checked or {}).get("patch")
        if not later or not patch:
            return ""
        text = (self.c.home / "objects" / f"{patch}.patch").read_bytes().decode(errors="replace")
        shown = "".join(d for path, d in repo.diffs(text).items() if path in later)
        if len(shown) > 20000:  # never cut silently: what is not shown here must still be read
            shown = shown[:20000] + f"\n\n[{len(shown) - 20000} more characters not shown: read the branch before merging it]"
        return (f'<div class="wide"><div class="label" style="color:var(--red)">These files run later, outside any '
                f'box <span>editors, agents, git, direnv, package managers or CI may run or obey them; read every '
                f'line</span></div><div class="box"><pre class="diff" style="padding:12px 0"><span>{esc(", ".join(sorted(later)))}'
                f'</span><span> </span>{diff_html(shown)}</pre></div></div>')

    def _pictures_part(self, run_id: str, st: RunState, target: Step[Any] | None) -> str:
        """The pictures the stop's step and the steps it needs left, such as references."""
        out = ""
        for x in [*target.needs, target] if target else []:
            files = self._files(st, x.name)
            pics = "".join(f'<a href="/file/{q(run_id)}/{q(x.name)}/{q(rel)}" target="_blank"><img src="/file/'
                           f'{q(run_id)}/{q(x.name)}/{q(rel)}" alt="{esc(rel)}" loading="lazy"><span>{esc(rel)}</span></a>'
                           for rel in files if Path(rel).suffix.lower() in PICTURES)
            if pics:
                out += (f'<div class="wide"><div class="label">Pictures from {esc(x.name)}</div>'
                        f'<div class="pictures">{pics}</div></div>')
        return out

    def _shown(self, run_id: str, st: RunState, shown: list[dict[str, Any]], items: list[tuple[str, str]]) -> str:
        """The stop's items as cards: their pictures, a title and a sentence. An item the person may choose is picked
        by clicking it, in order."""
        choosable = {i for i, _ in items}
        cards: list[str] = []
        for x in shown:
            pics: list[str] = []
            for pic in cast(list[dict[str, Any]], x.get("pictures") or []):
                step, rel = str(pic["step"]), str(pic["file"])
                obj = self._files(st, step).get(rel)
                if obj is None:
                    continue
                pics.append(self._picture(f"/file/{q(run_id)}/{q(step)}/{q(rel)}", self.c.home / "objects" / obj,
                                          str(pic.get("caption") or ""),
                                          [int(v) for v in cast(list[Any], pic.get("region") or [])]))
            row = f'<div class="pics">{"".join(pics)}</div>' if pics else ""
            ident = str(x.get("id") or "")
            pick = ident in choosable
            text = f"<p>{esc(str(x['text']))}</p>" if x.get("text") else ""
            cards.append(f'<div class="card{" pick" if pick else ""}"' + (f' data-id="{esc(ident)}" tabindex="0"' if pick else "")
                         + f'>{row}<div class="txt">' + ('<i class="n"></i>' if pick else "")
                         + f'<div><b>{esc(str(x["title"]))}</b>{text}</div></div></div>')
        return f'<div class="cards">{"".join(cards)}</div>'

    @staticmethod
    def _picture(link: str, path: Path, caption: str, region: list[int]) -> str:
        """A picture at a size its text can be read at: the part named by ``region``, never wider than its own
        pixels, a computer's across the column and a phone's two to a row. A click enlarges the whole of it here."""
        size = _size(path)
        if size is None:
            return (f'<a class="shot wide" href="{link}" data-zoom="{link}"><img src="{link}" alt="{esc(caption)}" '
                    f'loading="lazy">' + (f"<span>{esc(caption)}</span>" if caption else "") + "</a>")
        width, height = size
        x, y, w, h = region if len(region) == 4 and region[2] > 0 and region[3] > 0 else (0, 0, width, height)
        w, h = min(w, width - x), min(h, height - y)
        img = (f'<img src="{link}" alt="{esc(caption)}" loading="lazy" style="width:{width / w * 100:.3f}%;'
               f'margin-left:{-x / w * 100:.3f}%;margin-top:{-y / w * 100:.3f}%">')
        return (f'<a class="shot {"wide" if width >= height else "tall"}" href="{link}" data-zoom="{link}" '
                f'style="max-width:{w}px">' + (f"<span>{esc(caption)}</span>" if caption else "")
                + f'<div class="cut" style="aspect-ratio:{w}/{h}">{img}</div></a>')

    @staticmethod
    def _files(st: RunState, step: str) -> dict[str, str]:
        """The files a step's checked output carries: path to object."""
        checked = st.steps.get(step, StepState()).checked or {}
        return cast(dict[str, str], checked.get("files") or {})

    def picture(self, run_id: str, step: str, rel: str) -> tuple[bytes, str] | None:
        """A picture a step of the run left, with its type; other files are not served."""
        kind = PICTURES.get(Path(rel).suffix.lower())
        files = self._files(project(self.c.store.events(run_id=run_id)), step)
        if kind is None or rel not in files:
            return None
        return (self.c.home / "objects" / str(files[rel])).read_bytes(), kind

    def preview(self, run_id: str, which: str) -> tuple[int, str]:
        """Redirects to the run's preview once it answers; until then, a page that reloads itself."""
        st = project(self.c.store.events(run_id=run_id))
        info, spec = (st.started or {}).get("project"), (st.started or {}).get("preview")
        role = self.role_of(run_id)
        if not info or not spec or role is None or which not in ("before", "after"):
            return 404, "no preview for this run"
        patches = [] if which == "before" else [p for _, p in self.c.patches(role, st)]
        key = f"{run_id}-{which}-{hashlib.sha256(b''.join(patches)).hexdigest()[:12]}"
        folder = self.c.home / "runs" / run_id / "preview" / key

        def prepare() -> Path:
            if folder.exists():
                shutil.rmtree(folder)
            folder.parent.mkdir(parents=True, exist_ok=True)
            repo.prepare(Path(info["root"]), str(info["base"]), patches, folder,
                         cache=self.c.home / "runs" / run_id / "base")
            run_inputs.place(st.started or {}, folder)  # the run's frozen data, a clone each, never the live folder
            return folder

        try:
            p = self.shared.previews.ensure(key, prepare, Path(info["root"]), str(spec["command"]),
                                            str(spec.get("path", "/")), hidden=(self.c.home,),
                                            ports=tuple(int(x) for x in spec.get("ports", [])))
        except sandbox.Unavailable as err:
            state, text = "failed", str(err)
        else:
            state, text = Previews.state(p)
        if state == "ready":
            return 302, text
        head = "Starting the preview" if state == "starting" else "The preview failed"
        refresh = '<meta http-equiv="refresh" content="1">' if state == "starting" else ""
        return 200, (f'<!doctype html><html><head><meta charset="utf-8">{refresh}<style>body{{margin:0;padding:20px;'
                     f'font:13px/1.5 ui-monospace,Menlo,monospace;color:#5d6a7c;background:#fff}}pre{{white-space:pre-wrap}}'
                     f'</style></head><body><b>{head}</b><pre>{esc(text)}</pre></body></html>')

    def _send_back_bar(self, run_id: str, role: Role, st: RunState, step: str) -> str:
        """Send a blocked run back to a step with a note, or dismiss it. The step offered first is the last one
        before it that a person accepted: what they accepted is what most often blocks the steps after it."""
        names = [x.name for x in role.steps]
        upto = [x for x in role.steps[:names.index(step) + 1] if x.output is not None] if step in names else []
        accepted = [x.name for x in upto[:-1] if (st.step(x.name).released or {}).get("label") == "correct"]
        chosen = accepted[-1] if accepted else step
        options = "".join(f'<option value="{esc(x.name)}"{" selected" if x.name == chosen else ""}>{esc(x.name)}'
                          '</option>' for x in upto)
        return (f'<form class="bar" method="post" action="/send-back"><input type="hidden" name="token" '
                f'value="{esc(self.shared.token)}"><input type="hidden" name="run" value="{esc(run_id)}">'
                f'<div class="row"><input type="text" name="note" required placeholder="What was wrong? (needed to '
                f'send back)"><div class="acts"><select name="to" title="It and every step after it run again">'
                f'{options}</select><button class="btn" formaction="/dismiss" formnovalidate>Dismiss</button>'
                f'<button class="btn solid">Send back</button></div></div></form>')

    def _run_bar(self, run_id: str, cont: bool) -> str:
        """Dismiss, and Continue for a run whose process is gone: each button posts the same hidden fields."""
        go = '<button class="btn solid" formaction="/continue">Continue</button>' if cont else ""
        return (f'<form class="bar" method="post" action="/dismiss"><input type="hidden" name="token" '
                f'value="{esc(self.shared.token)}"><input type="hidden" name="run" value="{esc(run_id)}">'
                f'<div class="row"><span style="flex:1"></span><button class="btn">Dismiss</button>{go}</div></form>')

    def run_page(self, run_id: str) -> str:
        events = list(self.c.store.events(run_id=run_id))
        log = self.c.home / "runs" / run_id / "continue.log"
        if not events and log.is_file():  # started from the page; the page reloads on its first event
            out = log.read_text(errors="replace")[-4000:]
            main = (f'<div class="crumbs"><b>Starting</b><span class="r">{esc(run_id)}</span></div><div class="body">'
                    + (f'<div><div class="label">Output</div><div class="box" style="padding:14px 16px"><pre>{esc(out)}'
                       '</pre></div></div>' if out else '<div class="sub">Waiting for the run\'s first event.</div>')
                    + '</div>')
            return page(f"Run {run_id}", self.sidebar(None), main, self.seq())
        if not events:
            return self.not_found(f"No run {run_id}")
        st = project(events)
        role = self.role_of(run_id)
        names = self.names(role, events)
        started = st.started or {}
        state = (st.outcome if st.outcome in ("ended", "dismissed") else "finished" if st.finished else
                 "blocked" if st.blocked else "waiting" if any(x.open_stop for x in st.steps.values()) else
                 "paused" if st.paused_until and st.paused_until > self.c.clock() else
                 "running" if self.c._held(run_id) else "idle")  # pyright: ignore[reportPrivateUsage]
        t = totals(events)
        crumbs = (f'<div class="crumbs"><a href="/role/{q(str(started.get("role", "")))}">{esc(str(started.get("role", "")))}</a>/'
                  f'<b>{esc(headline(str(started.get("task", ""))))}</b><span class="r">{esc(run_id)}</span></div>')
        rows = "".join(self._event_row(ev) for ev in events)
        w = next((x for x in self.queue() if x.run_id == run_id and x.needs_person), None)
        call = ""  # what the run waits for, in one line, one click from it
        if w is not None:
            stop = self._question_of(w, role)
            say = {"stop": f"Waiting for you: {stop}", "blocked": f"Blocked at {w.step}: {w.reasons[0] if w.reasons else ''}",
                   "idle": "This run is not running: no process holds it."}[w.kind]
            label = {"stop": "Open the stop", "blocked": "See why", "idle": "Continue or dismiss"}[w.kind]
            call = (f'<div class="call {w.kind}"><span>{esc(say)}</span><a class="btn go" '
                    f'href="/stop/{q(run_id)}/{q(w.step)}">{label}</a></div>')
        made = (f'<div class="box"><table class="kv"><tr><td>branch</td><td class="m">{esc(str(st.branch["branch"]))} '
                f'in {esc(str(st.branch["root"]))}, at {esc(str(st.branch["commit"])[:8])}</td></tr></table></div>'
                if st.branch else "")
        task = str(started.get("task", ""))
        whole = (f'<details class="fold"><summary>The whole task</summary><div class="task">{esc(task)}</div></details>'
                 if headline(task) != task else "")
        body = (call + whole + f'<details class="fold"><summary>Steps <span>{len(names)}</span></summary>'
                f'{stage_list(stages(names, events, st, self.c.clock()))}</details>' + made
                + f'<details class="fold"><summary>Details <span>turns, tokens and events</span></summary>'
                f'<div class="tiles"><div><div class="n">{esc(state)}</div><p>run</p></div>'
                f'<div><div class="n">{t.turns}<small>turns</small></div><p>{tokens(t.tokens_in)} tokens in, {tokens(t.tokens_out)} out</p></div>'
                f'<div><div class="n">{age(t.seconds)}</div><p>from start to last event</p></div></div>'
                f'<div><div class="label">Events <span><a href="/log?run={q(run_id)}">raw log</a></span></div>'
                f'<div class="box"><table><tr><th>time</th><th>event</th><th>step</th><th>detail</th></tr>{rows}</table></div></div>'
                '</details>')
        main = crumbs + f'<div class="body">{body}</div>'
        return page(f"Run {run_id}", self.sidebar((run_id, w.step) if w else (run_id, "")), main, self.seq())

    def _question_of(self, w: Waiting, role: Role | None) -> str:
        """The question a stop asks, its step's own or the default the stop page shows."""
        if w.question:
            return w.question
        target = next((s for s in role.steps if s.name == w.step), None) if role else None
        needs = [n.name for n in target.needs] if target else []
        return (f"Do you accept the result of {', '.join(needs)}?" if target is not None and target.output is None
                and needs else f"Is the output of {w.step} right?")

    def known_roles(self) -> dict[str, dict[str, str]]:
        """Roles a run can be started with and their runtime overrides: those that come with Jaynes, then each one run."""
        known = {k: dict(v) for k, v in BUILT_IN.items()}
        for ev in self.c.store.events("RunStarted"):
            if "ref" in ev.payload:
                known[str(ev.payload["ref"])] = {str(k): str(v) for k, v in dict(ev.payload["runtimes"]).items()}
        return known

    @staticmethod
    def default_project() -> str:
        """The folder the page was opened from, when it is in a git repository."""
        try:
            return str(repo.resolve(Path.cwd())[0])
        except repo.ProjectError:
            return ""

    def new_page(self, error: str = "", fields: dict[str, str] | None = None) -> str:
        f = fields or {}
        last = next((str(ev.payload["ref"]) for ev in reversed(list(self.c.store.events("RunStarted")))
                     if ev.payload.get("ref")), DEMO)  # the role run last, else the demo
        chosen = f.get("role", last)
        options = "".join(f'<option value="{esc(ref)}"{" selected" if ref == chosen else ""}>'
                          f'{esc(f"{r.name} ({ref})" if (r := self.import_role(ref)) else ref)}</option>'
                          for ref in self.known_roles())
        main = ('<div class="crumbs"><b>New run</b></div><div class="body">'
                f'<form class="form" method="post" action="/start"><input type="hidden" name="token" value="{self.shared.token}">'
                f'<label><span class="label">Role</span><select name="role">{options}</select></label>'
                f'<label><span class="label">Or a role in this folder</span>'
                f'<input type="text" name="ref" placeholder="my_role.toml or my_role:ROLE" value="{esc(f.get("ref", ""))}">'
                f'<span class="m" style="margin-top:6px">{esc(str(Path.cwd()))}</span></label>'
                f'<label><span class="label">Task</span><textarea name="task" placeholder="Redesign the People screen">'
                f'{esc(f.get("task", ""))}</textarea></label>'
                f'<label><span class="label">Project folder</span>'
                f'<input type="text" name="project" placeholder="/path/to/repository" '
                f'value="{esc(f.get("project", self.default_project()))}"><span class="m" style="margin-top:6px">'
                f'A git repository. Steps get copies of its last commit; accepted changes come back as a branch.'
                f'</span></label>'
                f'<label><span class="label">Preview command</span>'
                f'<input type="text" name="preview" placeholder="npm run dev -- --port {{port}}" '
                f'value="{esc(f.get("preview", ""))}"><span class="m" style="margin-top:6px">Optional. Serves the '
                f'project on {{port}}; stops show it before and after.</span></label>'
                f'<label><span class="label">Preview path</span>'
                f'<input type="text" name="preview_path" placeholder="/" value="{esc(f.get("preview_path", ""))}">'
                f'</label>'
                f'<label><span class="label">Local ports the preview may reach</span>'
                f'<input type="text" name="preview_ports" placeholder="8789" '
                f'value="{esc(f.get("preview_ports", ""))}"><span class="m" style="margin-top:6px">Optional. The '
                f'preview runs the agent\'s changes in a box with no network; list the ports of local servers it '
                f'needs, such as a backend.</span></label>'
                f'<label><span class="label">Test suite</span>'
                f'<input type="text" name="suite" placeholder="npm test" value="{esc(f.get("suite", ""))}">'
                f'<span class="m" style="margin-top:6px">Optional. The command that runs all the project\'s tests, '
                f'for fix-bug, build-feature and small-change; Jaynes finds it when the project uses pytest, a test script, go, cargo or make.'
                f'</span></label>'
                f'<label><span class="label">Inputs</span>'
                f'<textarea name="inputs" placeholder="method=~/design/method.md">{esc(f.get("inputs", ""))}</textarea>'
                f'<span class="m" style="margin-top:6px">Only for roles that declare inputs, one name=value per line, '
                f'such as screen-design\'s method, heuristics, theme and data.</span></label>'
                + (f'<div class="err">{esc(error)}</div>' if error else '')
                + '<button class="btn go" type="submit">Start the run</button></form></div>')
        return page("New run", self.sidebar(None), main, self.seq())

    def history_page(self) -> str:
        if self.shared.history is None or time.time() - self.shared.history[0] > 300:  # reading takes seconds
            policy = load_policy(find_policy(str(Path.cwd())) or install.BUILT_IN_POLICY)
            self.shared.history = (time.time(), report(policy, past_calls(days=30)), policy.source)
        _, r, source = self.shared.history
        crumbs = '<div class="crumbs"><b>History</b><span class="r">last 30 days</span></div>'
        if r is None:
            main = crumbs + ('<div class="body" style="display:flex"><div class="empty"><h1>No tool calls found</h1>'
                             '<p>Claude Code transcripts from the last 30 days appear here.</p></div></div>')
            return page("History", self.sidebar(None), main, self.seq())
        alone = 1 - r.answered / r.calls
        kinds = {"floor": "blue", "pure": "green", "learns": "amber", "unknown": "red"}
        rows = "".join(
            f'<tr><td class="m">{esc(x.category)}</td><td><span class="chip {kinds[x.kind]}">{x.kind}</span></td>'
            f'<td class="m">{x.calls}</td><td class="m">{x.per_week:.0f}</td>'
            f'<td class="m">{esc(", ".join(f"{k} {v}" for k, v in x.approvers))}</td>'
            f'<td class="m">{x.audits_per_week:.0f}</td>'
            f'<td class="m">{"" if x.weeks_to_certify is None else f"{x.weeks_to_certify:.1f}"}</td></tr>'
            for x in r.rows)
        unknown = "".join(f'<tr><td class="m">{v}</td><td class="m">{esc(k)}</td></tr>' for k, v in r.unknown[:10])
        body = (f'<div><h1>What your agents did</h1><div class="sub">Read from your Claude Code transcripts and '
                f'classified by {esc(source)}. Nothing is changed.</div></div>'
                f'<div class="tiles"><div><div class="n">{r.calls}<small>tool calls</small></div>'
                f'<p>{r.calls / r.weeks:.0f} per week</p></div>'
                f'<div><div class="n">{r.answered}<small>answered by you</small></div>'
                f'<p>{r.answered / r.weeks:.1f} per week</p></div>'
                f'<div><div class="n">{alone:.1%}</div><p>ran without a person</p></div></div>'
                f'<div><div class="label">By category <span>with the gate: {r.audits_per_week:.0f} audit prompts '
                f'a week; a category is certified after {r.labels_to_certify} answers with no error</span></div>'
                f'<div class="box"><table><tr><th>category</th><th>kind</th><th>calls</th><th>per week</th>'
                f'<th>approved today by</th><th>audits per week</th><th>weeks to certify</th></tr>{rows}</table></div></div>'
                + (f'<div><div class="label">No category matches <span>add these to the policy</span></div>'
                   f'<div class="box"><table>{unknown}</table></div></div>' if unknown else ''))
        main = crumbs + f'<div class="body">{body}</div>'
        return page("History", self.sidebar(None), main, self.seq())

    def setup_page(self) -> str:
        g = install.status(self.c.home)
        chip = ('<span class="chip green">installed</span>' if g.installed else
                f'<span class="chip amber">partly installed, {len(g.hooked)} of {len(install.EVENTS)} events</span>'
                if g.hooked else '<span class="chip amber">not installed</span>')
        buttons = ('' if g.installed else '<button class="btn go" name="action" value="install">Install</button>') + \
            ('<button class="btn" name="action" value="remove">Remove</button>' if g.hooked else '')
        main = ('<div class="crumbs"><b>Setup</b></div><div class="body">'
                '<div><h1>Action gate in Claude Code</h1><div class="sub">Every tool call your Claude Code agents make '
                'goes through Jaynes. Actions that cannot be undone, like pushing, never run without you. Everything '
                'else runs as it does today, except a random one in ten that asks you, so Jaynes can measure.</div></div>'
                f'<div class="box"><table class="kv"><tr><td>state</td><td>{chip}</td></tr>'
                f'<tr><td>settings</td><td class="m">{esc(str(g.settings))}</td></tr>'
                f'<tr><td>policy</td><td class="m">{esc(str(g.policy))}'
                f'{"" if g.policy.is_file() else " (written on install)"}</td></tr></table></div>'
                + ('<div class="err">Jaynes is running from a temporary environment, so the hook would stop working '
                   'when it is cleaned. Install it first with uv tool install git+https://github.com/alk0x1/jaynes, '
                   'then open this page with jaynes.</div>' if install.temporary() and not g.installed else '')
                + f'<form method="post" action="/setup" class="checks"><input type="hidden" name="token" value="{self.shared.token}">'
                f'{buttons}</form>'
                f'<div class="sub">Install and Remove rewrite {esc(str(g.settings))} and keep the previous file as '
                f'{esc(g.settings.name)}.jaynes-backup. Your other hooks and settings stay as they are.</div>'
                + self._command_part() + self._version_part() + '</div>')
        return page("Setup", self.sidebar(None), main, self.seq())

    def _command_part(self) -> str:
        if skill.installed():
            state = '<span class="chip green">added</span>'
            button = '<button class="btn" name="action" value="skill-remove">Remove</button>'
        else:
            state = '<span class="chip amber">not added</span>'
            button = '<button class="btn go" name="action" value="skill">Add</button>'
        return ('<div><h2>/jaynes in Claude Code</h2><div class="sub">Type /jaynes and the task in Claude Code, in '
                'your project: the agent picks the role, starts the run, and this page calls you at its stops.</div>'
                f'</div><div class="box"><table class="kv"><tr><td>state</td><td>{state}</td></tr>'
                f'<tr><td>file</td><td class="m">{esc(str(skill.path()))}</td></tr></table></div>'
                f'<form method="post" action="/setup" class="checks"><input type="hidden" name="token" '
                f'value="{self.shared.token}">{button}</form>')

    def _version_part(self) -> str:
        at = update.RUNNING
        where = (f'{esc(at.short)} <span class="m">from {esc(at.url)}</span>' if at else
                 f'<span class="m">the files in {esc(str(Path(__file__).parent))}, not an install from git</span>')
        return (f'<div><h2>Version</h2></div><div class="box"><table class="kv"><tr><td>commit</td><td>{where}</td>'
                f'</tr></table></div>')

    def setup(self, fields: dict[str, str]) -> tuple[int, str]:
        action = fields.get("action", "")
        if action == "skill":
            skill.install()
        elif action == "skill-remove":
            skill.remove()
        elif action in ("install", "remove"):
            (install.install if action == "install" else install.remove)(self.c.home)
        else:
            return 400, self.setup_page()
        return 303, "/setup"

    def banner(self) -> str:
        """A newer Jaynes: one installed on disk since this page started, or one out at its address."""
        disk = update.installed()
        running = update.RUNNING
        if running is None or disk is None:
            return ""
        if disk.commit != running.commit:
            text, action, label = f"Jaynes was updated to {disk.short}.", "/restart", "Restart the page"
        elif self.shared.latest and self.shared.latest != disk.commit:
            text, action, label = "A newer Jaynes is out.", "/update", "Update"
        else:
            return ""
        return (f'<div class="notice new"><span>{text}</span><form method="post" action="{action}">'
                f'<input type="hidden" name="token" value="{self.shared.token}">'
                f'<button class="btn go">{label}</button></form></div>')

    def update(self, fields: dict[str, str]) -> tuple[int, str]:
        working = [w for w in self.queue() if w.kind == "running"]
        if working:  # a run imports parts of Jaynes as it goes, so the files must not change under it
            return 200, self.message("Update after the runs finish",
                                     f"{len(working)} run{' is' if len(working) == 1 else 's are'} working. "
                                     "Jaynes changes its files when it updates, so it waits for them.")
        at = update.installed()
        ok, out = update.reinstall(at) if at else (False, "Jaynes is not installed from git")
        if not ok:
            return 200, self.message("The update failed", out)
        if update.installed() == update.RUNNING:
            return 200, self.message("Jaynes is up to date", out)
        return 200, self.restarting()

    def restart(self, fields: dict[str, str]) -> tuple[int, str]:
        return 200, self.restarting()

    def restarting(self) -> str:
        main = ('<div class="crumbs"><b>Restarting</b></div><div class="body" style="display:flex">'
                '<div class="empty"><h1>Restarting Jaynes</h1><p>This tab comes back by itself in a moment.</p></div></div>')
        return page("Restarting", self.sidebar(None), main, self.seq())

    def message(self, title: str, text: str) -> str:
        main = (f'<div class="crumbs"><b>{esc(title)}</b></div><div class="body"><div><h1>{esc(title)}</h1>'
                f'<pre class="sub">{esc(text)}</pre></div></div>')
        return page(title, self.sidebar(None), main, self.seq())

    def roles_page(self) -> str:
        names = sorted({str(ev.payload["role"]) for ev in self.c.store.events("RunStarted")})
        rows = "".join(f'<tr><td><a href="/role/{q(n)}">{esc(n)}</a></td></tr>' for n in names)
        body = f'<div class="box"><table><tr><th>role</th></tr>{rows}</table></div>' if names else \
            '<div class="sub">No runs yet.</div>'
        main = f'<div class="crumbs"><b>Roles</b></div><div class="body">{body}</div>'
        return page("Roles", self.sidebar(None), main, self.seq())

    def role_page(self, name: str) -> str:
        runs = [ev.run_id for ev in self.c.store.events("RunStarted") if ev.payload["role"] == name and ev.run_id]
        if not runs:
            return self.not_found(f"No runs of role {name}")
        role = self.role_of(runs[-1])
        by_run = {r: list(self.c.store.events(run_id=r)) for r in runs}
        all_events = [ev for evs in by_run.values() for ev in evs]

        stop_rows: list[str] = []
        audit_rows: list[str] = []
        stop_steps = [s for s in role.steps if s.stop] if role else []
        for step in stop_steps:
            decided = [ev for ev in all_events if ev.type in ("StopOpened", "StopSkipped") and ev.payload["step"] == step.name]
            if not decided:
                stop_rows.append(f"<tr><td>{esc(step.name)}</td><td class='m'>no runs reached it</td><td></td><td></td><td></td></tr>")
                continue
            last = decided[-1].payload
            w = self.c.window(str(last["fingerprint"]))
            assert step.stop is not None
            # budget and delta as logged with the window, falling back to the code for logs before 0.4
            budget, delta = float(last.get("budget", step.stop.budget)), float(last.get("delta", step.stop.delta))
            status = ("floor" if not step.stop.sampled else "certified" if bounds.certified(w.n, w.k, budget, delta)
                      else "refuted" if bounds.refuted(w.n, w.k, budget, delta) else "undetermined")
            evidence = math.exp(bounds.log_certify_evalue(w.n, w.k, budget)) if w.n else 1.0
            skips = sum(1 for ev in decided if ev.type == "StopSkipped")
            audit_runs = [ev.run_id for ev in decided if ev.type == "StopOpened" and ev.payload["status"] == "certified"]
            for run in audit_runs:
                answer = next((ev.payload["answer"] for ev in by_run.get(run or "", [])
                               if ev.type == "StopAnswered" and ev.payload["step"] == step.name), "waiting")
                audit_rows.append(f'<tr><td>{esc(step.name)}</td><td><a href="/run/{q(run or "")}">{esc(run or "")}</a></td>'
                                  f'<td class="m">{esc(str(answer))}</td></tr>')
            stop_rows.append(
                f"<tr><td>{esc(step.name)}</td><td><span class='chip {CHIP.get(status, 'amber')}'>{esc(status)}</span></td>"
                f"<td class='m'>{w.n} label{'s' if w.n != 1 else ''}, {w.k} error{'s' if w.k != 1 else ''}, budget {budget:.0%}</td>"
                f"<td class='m'>{evidence:.2f} of {1 / delta:.0f}</td>"
                f"<td class='m'>{len(audit_runs)} audits, {skips} skipped</td></tr>")
        stops = (f'<div><div class="label">Stops</div><div class="box"><table><tr><th>step</th><th>status</th>'
                 f'<th>labels</th><th>evidence</th><th>audits</th></tr>{"".join(stop_rows)}</table></div></div>')
        if audit_rows:
            stops += (f'<div><div class="label">Audits <span>a certified stop that waited by the coin</span></div>'
                      f'<div class="box"><table><tr><th>step</th><th>run</th><th>answer</th></tr>'
                      f'{"".join(audit_rows)}</table></div></div>')

        weeks: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for evs in by_run.values():
            week = datetime.fromtimestamp(evs[0].ts).strftime("%G-W%V")
            wk = weeks[week]
            wk["runs"] += 1
            st = project(evs)
            if st.finished and st.outcome != "dismissed":
                wk["finished"] += 1
                wk["tokens"] += sum(int(ev.payload.get("tokens_in", 0)) + int(ev.payload.get("tokens_out", 0))
                                    for ev in evs if ev.type == "TurnFinished")
            wk["steps"] += len({ev.payload["step"] for ev in evs if ev.type == "Checked"})
            wk["rounds"] += sum(1 for ev in evs if ev.type == "StopAnswered" and ev.payload["answer"] in ("return", "context"))
            wk["stops"] += sum(1 for ev in evs if ev.type == "StopAnswered" and ev.payload["answer"] in ("accept", "change"))
            wk["seconds"] += sum(float(ev.payload.get("seconds_spent") or 0) for ev in evs if ev.type == "StopAnswered")
        week_rows = "".join(
            f"<tr><td class='m'>{esc(k)}</td><td class='m'>{v['runs']:.0f} ({v['finished']:.0f} finished)</td>"
            f"<td class='m'>{v['steps']:.0f}</td>"
            f"<td class='m'>{(1 + v['rounds'] / v['stops']) if v['stops'] else 0:.1f}</td>"
            f"<td class='m'>{tokens(int(v['tokens'] / v['finished'])) if v['finished'] else '-'}</td>"
            f"<td class='m'>{v['seconds'] / 60:.1f}</td></tr>" for k, v in sorted(weeks.items()))
        weekly = (f'<div><div class="label">Per week</div><div class="box"><table><tr><th>week</th><th>runs</th>'
                  f'<th>steps checked</th><th>rounds until accepted</th><th>tokens per finished run</th>'
                  f'<th>your minutes</th></tr>{week_rows}</table></div></div>')
        run_rows = "".join(
            f'<tr><td><a href="/run/{q(r)}">{esc(headline(str(evs[0].payload.get("task", ""))))}</a></td>'
            f'<td class="m">{esc(r)}</td></tr>' for r, evs in reversed(list(by_run.items())))
        runs_html = f'<div><div class="label">Runs</div><div class="box"><table>{run_rows}</table></div></div>'
        note = "" if role else '<div class="sub">The role module cannot be imported here, so stops are not listed.</div>'
        main = (f'<div class="crumbs"><a href="/roles">Roles</a>/<b>{esc(name)}</b></div>'
                f'<div class="body">{note}{stops}{weekly}{runs_html}</div>')
        return page(f"Role {name}", self.sidebar(None), main, self.seq())

    def log_page(self, kind: str | None, run_id: str | None) -> str:
        events = list(self.c.store.events(kind or None, run_id=run_id or None))[-500:]
        kinds = self.c.store.kinds()
        def link(k: str | None, text: str) -> str:
            q = urllib.parse.urlencode({x: y for x, y in (("type", k), ("run", run_id)) if y})
            on = " on" if k == kind else ""
            return f'<a class="{on.strip()}" href="/log{"?" + q if q else ""}">{esc(text)}</a>'
        filters = '<div class="filters">' + link(None, "all") + "".join(link(k, k) for k in kinds) + "</div>"
        rows = "".join(
            f'<tr><td class="m">{ev.seq}</td><td class="m">{datetime.fromtimestamp(ev.ts).strftime("%m-%d %H:%M:%S")}</td>'
            f'<td class="m">{esc(ev.type)}</td><td class="m">{esc(ev.run_id or ev.action_id or "")}</td>'
            f'<td><pre>{esc(json.dumps(ev.payload, ensure_ascii=False)[:600])}</pre></td></tr>' for ev in reversed(events))
        scope = f" for run {esc(run_id)}" if run_id else ""
        main = (f'<div class="crumbs"><b>Log</b><span>{scope}</span><span class="r">last {len(events)} events</span></div>'
                f'<div class="body wide">{filters}<div class="box"><table>'
                f'<tr><th>seq</th><th>time</th><th>type</th><th>run</th><th>payload</th></tr>{rows}</table></div></div>')
        return page("Log", self.sidebar(None), main, self.seq())

    # answering ----------------------------------------------------------------

    def answer(self, fields: dict[str, str]) -> tuple[int, str]:
        """Returns (status, location or page)."""
        run_id, step, raw = fields.get("run", ""), fields.get("step", ""), fields.get("answer", "")
        note = fields.get("note", "").strip() or None
        if raw not in LABELS:
            return 400, self.stop_page(run_id, step, f"unknown answer {raw!r}", fields)
        answer = cast(Answer, raw)
        if answer in ("return", "context") and not note:
            return 400, self.stop_page(run_id, step, f"{answer} needs a note for the agent", fields)
        role = self.role_of(run_id)
        if role is None:
            return 400, self.stop_page(run_id, step, "the role of this run cannot be imported here", fields)
        output = None
        if answer == "change":
            try:
                output = json.loads(fields.get("output", ""))
            except ValueError as err:
                return 400, self.stop_page(run_id, step, f"the edited output is not JSON: {err}", fields)
        opened = project(self.c.store.events(run_id=run_id)).steps.get(step, StepState()).open_stop
        seen = self.shared.first_seen.get((run_id, step, opened[0])) if opened else None
        spent = round(max(0.0, time.time() - seen), 3) if seen else None  # from the first view, across reloads
        try:
            self.c.answer(role, run_id, step, answer, note=note, output=output, seconds_spent=spent,
                          chosen=[x.strip() for x in fields.get("chosen", "").replace(",", " ").split()],
                          # the select goes with every answer; only return and context go back
                          to=(fields.get("to") or None) if answer in ("return", "context") else None,
                          by="person", evidence=fields.get("evidence") or None)
        except AnswerRejected as err:
            return 400, self.stop_page(run_id, step, "not recorded: " + "; ".join(err.reasons), fields)
        except RunBusy:
            return 409, self.stop_page(run_id, step, "the run is busy in another process; try again in a moment", fields)
        continue_in_background(self.c.home, run_id)
        return 303, f"/run/{q(run_id)}"

    def start(self, fields: dict[str, str]) -> tuple[int, str]:
        ref = fields.get("ref", "").strip() or fields.get("role", "")
        task = fields.get("task", "").strip()
        if not task:
            return 400, self.new_page("a run needs a task", fields)
        if ref.endswith(".toml"):  # stored whole, so the run continues from any folder; its mistakes are shown
            ref = str(Path(ref).expanduser().resolve())
            try:
                role_file.load(Path(ref))
            except role_file.RoleFileError as err:
                return 400, self.new_page(str(err), fields)
        r = self.import_role(ref)
        if r is None:
            return 400, self.new_page(f"{ref} cannot be imported from {Path.cwd()}", fields)
        if problems := verify.verify(r):
            return 400, self.new_page(verify.report(r, problems), fields)
        folder = fields.get("project", "").strip()
        preview = fields.get("preview", "").strip()
        if folder:
            try:
                repo.resolve(Path(folder).expanduser())
            except repo.ProjectError as err:
                return 400, self.new_page(str(err), fields)
        if preview and "{port}" not in preview:
            return 400, self.new_page("the preview command needs {port} where the port goes", fields)
        ports = fields.get("preview_ports", "").replace(",", " ").split()
        if not all(p.isdigit() and 0 < int(p) < 65536 for p in ports):
            return 400, self.new_page("local ports are numbers, such as 8789", fields)
        lines = [x.strip() for x in fields.get("inputs", "").splitlines() if x.strip()]
        if any("=" not in x for x in lines):
            return 400, self.new_page("inputs are name=value, one per line", fields)
        given = {k.strip(): v.strip() for k, v in (x.split("=", 1) for x in lines)}
        if problems := run_inputs.missing(r, given):
            return 400, self.new_page("; ".join(problems), fields)
        run_id = new_run_id()
        start_in_background(self.c.home, run_id, ref, task, self.known_roles().get(ref, {}),
                            str(Path(folder).expanduser()) if folder else "", preview,
                            fields.get("preview_path", "").strip(), " ".join(ports), fields.get("suite", "").strip(),
                            given)
        return 303, f"/run/{q(run_id)}"

    # pieces -------------------------------------------------------------------

    def resume(self, fields: dict[str, str]) -> tuple[int, str]:
        run_id = fields.get("run", "")
        if not list(self.c.store.events("RunStarted", run_id=run_id)):
            return 404, self.not_found(f"No run {run_id}")
        if self.c._held(run_id):  # pyright: ignore[reportPrivateUsage]
            return 409, self.run_page(run_id)
        continue_in_background(self.c.home, run_id)
        return 303, f"/run/{q(run_id)}"

    def send_back(self, fields: dict[str, str]) -> tuple[int, str]:
        run_id = fields.get("run", "")
        role = self.role_of(run_id)
        if role is None:
            return 404, self.not_found(f"No role to send run {run_id} back with")
        try:
            self.c.send_back(role, run_id, fields.get("to", ""), fields.get("note", ""), by="person")
        except AnswerRejected as err:
            return 400, self.not_found("; ".join(err.reasons))
        except RunBusy:
            return 409, self.run_page(run_id)
        continue_in_background(self.c.home, run_id)
        return 303, f"/run/{q(run_id)}"

    def dismiss(self, fields: dict[str, str]) -> tuple[int, str]:
        run_id = fields.get("run", "")
        try:
            self.c.dismiss(run_id)
        except AnswerRejected as err:
            return 400, self.not_found("; ".join(err.reasons))
        except RunBusy:
            return 409, self.run_page(run_id)
        return 303, "/"

    def _value(self, s: StepState, target: Step[Any] | None) -> Any:
        return None if s.checked is None else self._value_of(s.checked, target)

    def _value_of(self, checked: dict[str, Any], target: Step[Any] | None) -> Any:
        """A checked output, with fields in schema order when the role is known (stored bytes sort keys)."""
        if checked.get("output_hash") is None:
            return None
        raw = (self.c.home / "objects" / f"{checked['output_hash']}.json").read_text()
        if target is not None and target.output is not None:
            return target.output.model_validate_json(raw).model_dump(mode="json")
        return json.loads(raw)

    @staticmethod
    def _where(s: StepState) -> str:
        return f"round {s.round}" + (f", attempt {s.checked['attempt']}" if s.checked and s.checked.get("attempt") else "")

    @staticmethod
    def _round_of(events: list[Event], target: Event) -> int:
        """The round a step event belongs to: the round of the last StepStarted of that step before it."""
        r = 1
        for ev in events:
            if ev.seq > target.seq:
                break
            if ev.type == "StepStarted" and ev.payload["step"] == target.payload["step"]:
                r = int(ev.payload["round"])
        return r

    def _turns(self, events: list[Event], step: str, round_: int) -> str:
        rows: list[str] = []
        tin = tout = 0
        seconds = 0.0
        models: set[str] = set()
        for ev in events:
            p = ev.payload
            if p.get("step") != step or self._round_of(events, ev) != round_:
                continue
            if ev.type == "TurnFinished":
                if "web" in p:
                    read = ", ".join(str(x) for x in p["web"]) or "nothing"
                    rows.append(f'<tr><td class="m" style="color:var(--blue)">web</td><td>read {esc(read)}</td>'
                                f'<td class="m">attempt {esc(str(p["attempt"]))}</td></tr>')
                for r in p.get("ran", []):
                    command = f'<code>{esc(str(r["command"]))}</code>'
                    shown = (f'<details class="ran"><summary>{command}</summary><pre>{esc(str(r["output"]))}</pre></details>'
                             if r["output"] else command)
                    rows.append(f'<tr><td class="m" style="color:var(--{"green" if r["ok"] else "red"})">ran</td>'
                                f'<td>{shown}</td><td class="m">attempt {esc(str(p["attempt"]))}, '
                                f'{"ok" if r["ok"] else "failed"}</td></tr>')
                tin += int(p["tokens_in"])
                tout += int(p["tokens_out"])
                seconds += float(p.get("seconds") or 0)
                models.add(str(p["model"]))
            elif ev.type == "TurnRejected":
                reasons = "; ".join(str(x) for x in p.get("reasons", [])[:3])
                rows.append(f'<tr><td class="m" style="color:var(--red)">rejected</td><td>{esc(reasons)}</td>'
                            f'<td class="m">attempt {esc(str(p["attempt"]))}, {esc(str(p["reason"]))}</td></tr>')
            elif ev.type == "TurnInterrupted":
                rows.append(f'<tr><td class="m">interrupted</td><td>{esc(str(p["reason"]))}</td>'
                            f'<td class="m">attempt {esc(str(p["attempt"]))}</td></tr>')
            elif ev.type == "Checked":
                rows.append(f'<tr><td class="m" style="color:var(--green)">checked</td><td>by {esc(str(p["source"]))}</td>'
                            f'<td class="m">attempt {esc(str(p["attempt"]))}</td></tr>')
        cost = (f"{tokens(tin)} tokens in, {tokens(tout)} out, {age(seconds)}"
                + (f", {esc(', '.join(sorted(models)))}" if models else ""))
        return (f'<div><div class="label">Turns <span>{cost}</span></div>'
                f'<div class="box"><table>{"".join(rows)}</table></div></div>')

    def _tiles(self, s: StepState, target: Step[Any] | None) -> str:
        if s.open_stop is None:
            return ""
        p = s.open_stop[1]
        n, k = int(p["n"]), int(p["k"])
        status = str(p["status"])
        if target is None or target.stop is None:
            return ""
        sp = target.stop
        evidence = math.exp(float(p["log_e"])) if n else 1.0
        budget, delta = float(p.get("budget", sp.budget)), float(p.get("delta", sp.delta))
        need = bounds.labels_to_certify(k, budget, delta)
        if status == "floor":
            third = '<div><div class="n">always</div><p>a floor waits on every run</p></div>'
        elif status == "certified":
            third = f'<div><div class="n">{sp.audit_rate:.0%}</div><p>an audit: this stop is sampled</p></div>'
        else:
            more = "never" if need is None else str(max(need - n, 0))
            third = f'<div><div class="n">{more}</div><p>more clean answers needed</p></div>'
        return (f'<div><div class="label">This stop <span class="chip {CHIP.get(status, "amber")}">{esc(status)}</span>'
                f'</div><div class="tiles">'
                f'<div><div class="n">{n}<small>label{"s" if n != 1 else ""}</small></div>'
                f'<p>{k} error{"s" if k != 1 else ""}, budget {budget:.0%}</p></div>'
                f'<div><div class="n">{evidence:.2f}<small>of {1 / delta:.0f}</small></div><p>evidence to sample it</p></div>'
                f"{third}</div></div>")

    @staticmethod
    def _output(label: str, value: Any, right: str = "") -> str:
        name = ""
        if isinstance(value, dict) and len(cast(dict[str, Any], value)) == 1:
            name, value = next(iter(cast(dict[str, Any], value).items()))  # one field: its name joins the label
        return (f'<div><div class="label">{esc(label)}{": " + esc(name) if name else ""} <span>{esc(right)}</span></div>'
                f"{render_value(value)}</div>")

    @staticmethod
    def _earlier(role: Role | None, step: str) -> list[str]:
        """Steps before ``step`` with an output, which a return or context from its stop can go back to."""
        names = [x.name for x in role.steps] if role else []
        return [x.name for x in role.steps[:names.index(step)] if x.output is not None] if role and step in names else []

    def _items(self, target: Step[Any] | None, s: StepState) -> list[tuple[str, str]]:
        """The items a person may choose among at this stop, as (id, label); none where the step lets nobody."""
        if target is None or target.choose is None or target.output is None or s.checked is None:
            return []
        try:
            return [(str(i), str(label)) for i, label in target.choose.items(
                target.output.model_validate_json(self.c.home.joinpath("objects", f"{s.checked['output_hash']}.json")
                                                  .read_bytes()))]
        except Exception:
            return []

    def _answer_bar(self, run_id: str, step: str, value: Any, error: str, fields: dict[str, str] | None,
                    editable: bool = True, earlier: list[str] | None = None, evidence: str = "",
                    default_to: str | None = None, floor: bool = False,
                    items: list[tuple[str, str]] | None = None) -> str:
        """Accept and Return, with the note, in sight; Change, Context and going back to an earlier step in a menu.
        Where the person may choose, the cards they click fill ``chosen`` and Accept becomes Choose."""
        fields = fields or {}
        chosen = fields.get("to", "") or (default_to or "")
        back = ("" if not earlier else
                '<label>Return and context go to<select name="to" title="Every step after it runs again">'
                + ("" if default_to else f'<option value="">{esc(step)}</option>')  # a step with no output reruns nothing
                + "".join(f'<option value="{esc(n)}"{" selected" if n == chosen else ""}>{esc(n)}</option>'
                          for n in earlier) + "</select></label>")
        edited = fields.get("output") or json.dumps(value, indent=2, ensure_ascii=False)
        editor_class = "" if fields.get("answer") == "change" else "hidden"
        hidden = "".join(f'<input type="hidden" name="{k}" value="{esc(v)}">' for k, v in (
            ("token", self.shared.token), ("run", run_id), ("step", step), ("evidence", evidence), ("answer", ""),
            ("chosen", fields.get("chosen", "") if items else "")))
        floor_mark = ' data-floor="1"' if floor else ""  # the page script asks for A twice
        menu = (f'<details class="menu"><summary class="btn">More</summary><div class="pop">{back}'
                f'<button type="button" class="btn" data-a="context">Context: add facts, same output<kbd>X</kbd></button>'
                + (f'<button type="button" class="btn" data-a="change">Change: edit the output yourself<kbd>C</kbd>'
                   f'</button>' if editable else "") + '</div></details>')
        return (f'<form id="answer" class="bar" method="post" action="/answer"{floor_mark}>{hidden}'
                f'<div id="editor" class="{editor_class}"><div class="label">Edit the output, then press Change again '
                f'<span>it is checked before it replaces the agent\'s</span></div>'
                f'<textarea name="output" spellcheck="false">{esc(edited)}</textarea></div>'
                f'<div class="row"><input type="text" name="note" value="{esc(fields.get("note", ""))}" '
                f'placeholder="What is wrong? (needed to return)">'
                f'<div class="acts">{menu}'
                f'<button type="button" class="btn" data-a="return">Return<kbd>R</kbd></button>'
                f'<button type="button" class="btn solid" data-a="accept">Accept<kbd>A</kbd></button></div></div>'
                f'<div id="hint" class="err">{esc(error)}</div></form>')

    def _event_row(self, ev: Event) -> str:
        p = ev.payload
        detail = {
            "RunStarted": lambda: f"task {p.get('task', '')}",
            "StepStarted": lambda: f"round {p['round']}, attempt {p['attempt']}, by {p['executor']}",
            "TurnFinished": lambda: f"{tokens(int(p['tokens_in']))} in, {tokens(int(p['tokens_out']))} out, "
                                    f"{age(float(p.get('seconds') or 0))}, {p['model']}"
                                    + (f", ran {len(p['ran'])} commands" if p.get("ran") else ""),
            "TurnRejected": lambda: f"{p['reason']}: " + "; ".join(str(x) for x in p.get("reasons", [])[:2]),
            "TurnInterrupted": lambda: str(p["reason"]),
            "Checked": lambda: f"by {p['source']}" + (f", output {str(p['output_hash'])[:12]}" if p.get("output_hash") else ""),
            "StopOpened": lambda: f"{p['status']}, {p['n']} labels, {p['k']} errors",
            "StopSkipped": lambda: ("small change, not asked" if p["status"] == "small" else
                                    f"certified, coin {float(p['coin']):.2f}"),
            "StopAnswered": lambda: (f"{p['answer']} to {p['to']}" + (f" ({p['revises']['label']} for {p['to']})"
                                                                      if p.get("revises") else "")
                                     if p.get("to") else
                                     f"{'accepted' if p['answer'] == 'accept' else p['answer']} ({p['label']})")
                                    + (f": {p['note']}" if p.get("note") else ""),
            "RunPaused": lambda: f"quota until {datetime.fromtimestamp(float(p['until'])).strftime('%H:%M')}",
            "RunBlocked": lambda: "; ".join(str(x) for x in p.get("reasons", [])[:2]),
            "RunSentBack": lambda: f"from {p['step']} to {p['to']}, by {p['by']}: {p['note']}",
            "RunFinished": lambda: (f"ended at {p['step']}; nothing was delivered" if p.get("outcome") == "ended"
                                    else "dismissed; nothing was delivered" if p.get("outcome") == "dismissed"
                                    else "finished"),
        }.get(ev.type, lambda: "")()
        when = datetime.fromtimestamp(ev.ts).strftime("%H:%M:%S")
        return (f'<tr><td class="m">{when}</td><td class="m">{esc(ev.type)}</td><td class="m">{esc(str(p.get("step", "")))}</td>'
                f"<td>{esc(detail)}</td></tr>")

    def not_found(self, text: str) -> str:
        main = f'<div class="crumbs"><b>{esc(text)}</b></div><div class="body"></div>'
        return page(text, self.sidebar(None), main, self.seq())


# --- HTTP ------------------------------------------------------------------------------

def kept_key(home: Path, key: str | None = None) -> str:
    """The key that lets a browser answer as the person, kept in ``home`` so the browser that held it still answers
    after the page starts again from nothing, as after a reboot. ``key``, the one a restart hands over, replaces
    the kept one, since the open tab holds it. Only the person's user may read the file."""
    path = home / "review.key"
    if key is None:
        try:
            return path.read_text().strip()
        except FileNotFoundError:
            key = secrets.token_urlsafe(24)
    elif path.is_file() and path.read_text().strip() == key:
        return key
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key


def make_server(home: Path, port: int = 8740, idle_after: float = 10.0, wake_every: float = 15.0,
                call: bool = False, key: str | None = None) -> ReviewServer:
    """A review server for ``home`` on 127.0.0.1; ``port=0`` picks a free one. Every ``wake_every`` seconds it
    continues the runs whose quota pause has ended, since nothing else would. With ``call``, it is the person's
    page: it opens the browser at a new stop when no tab is open, and looks for a newer Jaynes. ``key`` is the one a
    restart keeps, so the browser that held it still answers."""
    if str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))  # role modules are imported from the project, as `jaynes run` does
    shared = Shared(secrets.token_urlsafe(16), kept_key(home, key), idle_after)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return None

        def _send(self, status: int, body: str | bytes, kind: str = "text/html; charset=utf-8",
                  location: str | None = None, frame: str = "DENY", cookie: str | None = None) -> None:
            if isinstance(body, str) and kind.startswith("text/html"):
                notice = (f'<div class="notice">{NOT_PERSON}</div>' if not self._person() else
                          Review(home, shared).banner())
                body = body.replace("<main>", f"<main>{notice}", 1).replace(
                    "<body>", f'<body data-boot="{shared.token}">', 1)
            data = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", frame)
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(data)

        def _local(self) -> bool:
            port = cast(tuple[str, int], self.server.server_address)[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            origin = self.headers.get("Origin")
            return self.headers.get("Host") in hosts and (origin is None or origin.split("//", 1)[-1] in hosts)

        def _cookie_name(self) -> str:  # cookies ignore ports, so two servers keep apart by name
            return f"jaynes_key_{cast(tuple[str, int], self.server.server_address)[1]}"

        def _person(self) -> bool:
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie") or "")
            except CookieError:
                return False
            got = jar.get(self._cookie_name())
            return got is not None and secrets.compare_digest(got.value, shared.key)

        def do_GET(self) -> None:
            try:
                self._get()
            except BrokenPipeError:
                pass
            except Exception as err:  # answer, rather than drop the connection
                self._send(500, f"error: {type(err).__name__}: {err}", "text/plain")

        def do_POST(self) -> None:
            try:
                self._post()
            except BrokenPipeError:
                pass
            except Exception as err:
                self._send(500, f"error: {type(err).__name__}: {err}", "text/plain")

        def _get(self) -> None:
            if not self._local():
                return self._send(403, "forbidden", "text/plain")
            app = Review(home, shared)
            url = urllib.parse.urlsplit(self.path)
            parts = [urllib.parse.unquote(x) for x in url.path.strip("/").split("/") if x]
            query = dict(urllib.parse.parse_qsl(url.query))
            if "key" in query:  # the link the server printed: keep the key as a cookie and drop it from the address
                if not secrets.compare_digest(query["key"], shared.key):
                    return self._send(403, "this link is from an earlier start of jaynes; open the one it printed "
                                           "last", "text/plain")
                rest = urllib.parse.urlencode({k: v for k, v in query.items() if k != "key"})
                return self._send(303, "", location=url.path + (f"?{rest}" if rest else ""), cookie=(
                    f"{self._cookie_name()}={shared.key}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"))
            match parts:
                case []:
                    self._send(200, app.queue_page())
                case ["seq"]:  # also says which start of the server answers, so a tab sees a restart
                    if self._person():
                        shared.polled = time.time()
                    self._send(200, f"{app.seq()} {shared.token}", "text/plain")
                case ["stop", run_id, step]:
                    self._send(200, app.stop_page(run_id, step))
                case ["stop", run_id]:  # a run blocked with no step, in logs from before it had one
                    self._send(200, app.run_page(run_id))
                case ["run", run_id]:
                    self._send(200, app.run_page(run_id))
                case ["new"]:
                    self._send(200, app.new_page())
                case ["preview", run_id, which]:
                    status, out = app.preview(run_id, which)
                    if status == 302:
                        self._send(302, "", location=out)
                    else:
                        self._send(status, out, frame="SAMEORIGIN")
                case ["file", run_id, step, *rel] if rel:
                    found = app.picture(run_id, step, "/".join(rel))
                    if found is None:
                        self._send(404, app.not_found("Not found"))
                    else:
                        self._send(200, found[0], found[1])
                case ["history"]:
                    self._send(200, app.history_page())
                case ["setup"]:
                    self._send(200, app.setup_page())
                case ["roles"]:
                    self._send(200, app.roles_page())
                case ["role", name]:
                    self._send(200, app.role_page(name))
                case ["log"]:
                    self._send(200, app.log_page(query.get("type"), query.get("run")))
                case _:
                    self._send(404, app.not_found("Not found"))

        def _post(self) -> None:
            if not self._local():
                return self._send(403, "forbidden", "text/plain")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= 2_000_000:
                return self._send(400, "bad Content-Length", "text/plain")
            fields = dict(urllib.parse.parse_qsl(self.rfile.read(length).decode(errors="replace"), keep_blank_values=True))
            path = urllib.parse.urlsplit(self.path).path
            if path == "/bye":  # a tab closing or reloading; a reload asks for /seq again at once
                if self._person():
                    shared.polled = 0.0
                return self._send(204, b"", "text/plain")
            handlers = {"/answer": Review.answer, "/continue": Review.resume, "/dismiss": Review.dismiss,
                        "/send-back": Review.send_back,
                        "/start": Review.start, "/setup": Review.setup, "/update": Review.update,
                        "/restart": Review.restart}
            if path not in handlers:
                return self._send(404, "not found", "text/plain")
            if not secrets.compare_digest(fields.get("token", ""), shared.token):
                return self._send(403, "this page is stale or not from this server; reload it", "text/plain")
            if not self._person():
                return self._send(403, NOT_PERSON, "text/plain")
            app = Review(home, shared)
            status, out = handlers[path](app, fields)
            if status == 303:
                self._send(303, "", location=out)
            else:
                self._send(status, out)
            if path in ("/update", "/restart") and update.installed() != update.RUNNING:
                threading.Timer(0.5, cast(ReviewServer, self.server).restart).start()

    server = ReviewServer(("127.0.0.1", port), Handler, shared.previews, shared.key, home)
    server.shared = shared
    threading.Thread(target=_wake, args=(home, wake_every, server.stopping), daemon=True).start()
    if call:
        threading.Thread(target=_call, args=(home, shared, server.link, server.stopping), daemon=True).start()
        threading.Thread(target=_look, args=(shared, server.stopping), daemon=True).start()
    return server


def _call(home: Path, shared: Shared, link: str, stopping: threading.Event, every: float = 2.0,
          alive: float = 75.0) -> None:
    """Open the page when something new needs the person and no tab is open; an open tab notifies by itself. A
    hidden tab may ask for /seq only once a minute, so a tab counts as open for ``alive`` seconds after it asked."""
    import webbrowser
    seen: set[tuple[str, str, float]] | None = None
    while not stopping.wait(every):
        try:
            now = {(w.run_id, w.step, w.since) for w in Conductor(home).queue(idle_after=shared.idle_after)
                   if w.needs_person}
        except Exception:  # a locked or half-written log is read again on the next tick
            continue
        if seen is not None and now - seen and time.time() - shared.polled > alive:
            webbrowser.open(link)
        seen = now


def _look(shared: Shared, stopping: threading.Event, every: float = 6 * 3600) -> None:
    """What Jaynes's address points at, now and every ``every`` seconds, for the page's banner."""
    at = update.RUNNING
    while at is not None:
        shared.latest = update.latest(at.url) or shared.latest
        if stopping.wait(every):
            return


def ensure_page(home: Path, port: int = 8740) -> None:
    """Start ``home``'s review page in the background, opened in the browser, unless it already runs. Its link,
    which lets a browser answer as the person, goes only to the browser, never to a log an agent reads."""
    if os.environ.get("JAYNES_NO_PAGE") or _running(home.resolve(), port) is not None:
        return
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [os.getcwd(), os.environ.get("PYTHONPATH")])))
    with (home / "review.log").open("ab") as log:
        subprocess.Popen([sys.executable, "-m", "jaynes.cli", "review", "--home", str(home), "--port", str(port),
                          "--open"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, env=env,
                         start_new_session=True)


def _wake(home: Path, every: float, stopping: threading.Event) -> None:
    """Continue each run whose pause ended, once per reset: one that pauses again waits for its new reset."""
    tried: set[tuple[str, float]] = set()
    while not stopping.wait(every):
        try:
            for due in Conductor(home).due():
                if due not in tried:
                    tried.add(due)
                    continue_in_background(home, due[0])
        except Exception:  # a locked or half-written log is read again on the next tick
            continue


KEPT = "JAYNES_REVIEW_KEY"  # the key a restart hands to the start that replaces it, never to a run
NOT_PERSON = ("This browser can look but not answer. To answer, open the link jaynes printed in its terminal "
              "when it started.")


class ReviewServer(ThreadingHTTPServer):
    """Stops the previews it started when it shuts down. ``link`` opens the page as the person."""

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], previews: Previews,
                 key: str, home: Path) -> None:
        self.previews = previews  # set first: a port in use closes the server inside __init__
        self.key = key
        self.home = home
        self.shared: Shared | None = None
        self.stopping = threading.Event()  # ends the thread that continues paused runs
        super().__init__(address, handler)

    @property
    def link(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?key={self.key}"

    def shutdown(self) -> None:
        super().shutdown()
        self.stopping.set()
        self.previews.stop_all()

    def server_close(self) -> None:
        super().server_close()
        self.stopping.set()
        self.previews.stop_all()

    def restart(self) -> None:
        """Become the Jaynes installed now, in this same process, so whoever started the page still holds it. The
        new start keeps the key, so the open tab, which sees the restart, reloads into it and still answers, and no
        new tab opens; the socket closes on exec."""
        self.previews.stop_all()
        os.execve(sys.executable, [sys.executable, "-m", "jaynes.cli", "review", "--home", str(self.home),
                                   "--port", str(self.server_address[1]), "--open"],
                  {**os.environ, KEPT: self.key})


def _running(home: Path, port: int) -> int | None:
    """The process serving ``home``'s page on ``port``, if one still is."""
    try:
        info = json.loads((home / "review.json").read_text())
        pid = int(info["pid"])
        os.kill(pid, 0)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return pid if info.get("port") == port else None


def serve(home: Path, port: int, browser: bool = False, point: bool = True) -> None:
    """Serve the page; ``browser`` opens it with its link, as the person, and makes it call the person at stops.
    If ``home``'s page already runs on ``port``, ``point`` to it instead: its key stays in the browser that
    opened it first."""
    home = home.resolve()
    home.mkdir(parents=True, exist_ok=True)  # a new home: the page records itself there before any request
    skill.refresh()
    kept = os.environ.pop(KEPT, None)  # a restart: the tab that was open moves to it by itself
    try:
        server = make_server(home, port, call=browser, key=kept)
    except OSError as err:
        if err.errno != errno.EADDRINUSE:
            raise
        pid = _running(home, port)
        if not point:  # started for a run, and another start won the port
            return
        if pid is None:
            sys.exit(f"port {port} is in use by another program or another Jaynes home; stop it or pass --port")
        print(f"Jaynes review is already running on http://127.0.0.1:{port}/ (process {pid})", flush=True)
        if browser:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return
    mine, port = home / "review.json", server.server_address[1]  # port 0 picks a free one
    mine.write_text(json.dumps({"pid": os.getpid(), "port": port}))

    def leave(signum: int, frame: object) -> None:
        sys.exit(0)

    for sig in (signal.SIGTERM, signal.SIGHUP):  # a closed terminal or a kill still stops the previews
        signal.signal(sig, leave)
    print(f"Jaynes review on {server.link}  (Ctrl+C to stop)", flush=True)
    if browser and not kept:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, [server.link]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if _running(home, port) == os.getpid():
            mine.unlink(missing_ok=True)
