"""What steps about a product's screens share: serving a copy of it, and the facts and citations agents may use.

``serve`` runs the run's preview command on a copy of the project, in a box (``jaynes.sandbox``), with a fresh clone
of the run's frozen data in place, if it has any, and yields the origin Jaynes's Chrome (``jaynes.browser``) looks at.
Never the person's folder. What data the product sees is the role's promise, not this module's: screen-design freezes
a copy, so it never touches live data; build-feature gives none, so a product that reads the person's files shows them.

Agents see a summary of each API response, not the response: how many items, how values are spread, a few samples
with their ids. A number an agent writes goes in a ``Count``, which names a query over the raw response; Jaynes
computes it, so a number cannot be made up, and an agent that does not know one leaves it empty and is told it.
Prose with loose digits is refused, apart from dates, ids and words quoted from what Jaynes captured.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, Field

from jaynes import inputs, sandbox
from jaynes.preview import READY_WITHIN, free_port, link_dependencies
from jaynes.role import Blocked, Fail, Pass, Witnesses

SAMPLES, SPREAD, MAX_FACTS = 3, 12, 300
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
ID = re.compile(r"\b[A-Z]{1,2}\d+(?:-s\d+)?\b")


# --- the closed vocabulary -------------------------------------------------------

class _Act(BaseModel):
    note: str = Field(default="", max_length=200, description="what the owner is trying to do here")


class Open(_Act):
    action: Literal["open"]
    to: str = Field(pattern=r"^/[^\s]{0,300}$", description="a path on the product, such as /?view=books")


class ClickText(_Act):
    action: Literal["clickText"]
    text: str = Field(min_length=1, max_length=120, description="visible text of what is clicked")
    exact: bool = False


class OpenRow(_Act):
    action: Literal["openRow"]
    text: str = Field(min_length=1, max_length=120, description="text in the row of a list or table")


class PressButton(_Act):
    action: Literal["pressButton"]
    name: str = Field(min_length=1, max_length=120, description="the button's name, as a screen reader says it")


class Scroll(_Act):
    action: Literal["scroll"]
    region: str = Field(default="page", pattern=r"^(page|[.#]?[A-Za-z][\w-]{0,60})$")
    to: str = Field(default="bottom", pattern=r"^(top|bottom|\d{1,5})$")


class Type(_Act):
    action: Literal["type"]
    field: str = Field(min_length=1, max_length=80, description="the field's label or placeholder")
    text: str = Field(max_length=120)


class Back(_Act):
    action: Literal["back"]


class ExpectText(_Act):
    action: Literal["expectText"]
    text: str = Field(min_length=1, max_length=200)


class ExpectRoute(_Act):
    action: Literal["expectRoute"]
    route: str = Field(pattern=r"^/", description="where the page is, <name> standing for any one value")


WalkAction = Annotated[Open | ClickText | OpenRow | PressButton | Scroll | Type | Back, Field(discriminator="action")]
TestAction = Annotated[Open | ClickText | OpenRow | PressButton | Scroll | Type | Back | ExpectText | ExpectRoute,
                       Field(discriminator="action")]


# --- facts and citations ----------------------------------------------------------

class Cond(BaseModel):
    field: str
    equals: str | int | float | bool | None


class Query(BaseModel):
    endpoint: str = Field(description="the summary file of the response, as the snapshot lists it, e.g. api/books.json")
    items: str = Field(default="", description="dot path to the list in the response; empty when it is the list")
    where: list[Cond] = Field(default_factory=list[Cond], description="keep items whose field equals the value")
    count: Literal["items", "distinct"] = "items"
    field: str = Field(default="", description="with distinct: the field whose different values are counted")


class Count(BaseModel):
    query: Query
    value: int | None = Field(default=None, description="the number; leave it empty when you do not know it, and "
                                                        "Jaynes says it when it rejects the attempt")


class Fact(BaseModel):
    id: str
    query: Query
    value: int


class Cite(BaseModel):
    file: str = Field(description="a file of the snapshot, such as screen.txt or api/books.json")
    match: str = Field(min_length=3, description="words copied exactly from that file")


def summary_name(path: str) -> str:
    """The summary file of a response at ``path``."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", path.split("?")[0]).strip("-") or "root"
    return f"api/{slug[:80]}.json"


def find(value: Any, path: str) -> Any:
    for part in [p for p in path.split(".") if p]:
        value = cast(list[Any], value)[int(part)] if isinstance(value, list) else cast(dict[str, Any], value)[part]
    return value


def compute(q: Query, raw: Mapping[str, Any]) -> int:
    """The number a query names, over the raw responses by summary file; raises KeyError or ValueError."""
    if q.endpoint not in raw:
        raise KeyError(f"no response {q.endpoint}; they are {', '.join(sorted(raw)) or 'none'}")
    found = find(raw[q.endpoint], q.items)
    if not isinstance(found, list):
        raise ValueError(f"{q.items or 'the response'} in {q.endpoint} is not a list")
    items = cast(list[Any], found)
    rows = [cast(dict[str, Any], i) for i in items if isinstance(i, dict)]
    if q.count == "distinct":
        if not q.field:
            raise ValueError("a distinct count names its field")
        kept = [r for r in rows if all(r.get(c.field) == c.equals for c in q.where)]
        return len({json.dumps(r.get(q.field), sort_keys=True) for r in kept})
    return len([r for r in rows if all(r.get(c.field) == c.equals for c in q.where)]) if q.where else len(items)


def summarize(path: str, body: Any) -> tuple[dict[str, Any], list[Fact]]:
    """What an agent reads of a response: its lists, their sizes, how values spread, and a few samples."""
    name = summary_name(path)
    lists: list[tuple[str, list[Any]]] = []

    def walk(v: Any, where: str, depth: int) -> None:
        if isinstance(v, list):
            lists.append((where, cast(list[Any], v)))
        elif isinstance(v, dict) and depth < 3:
            for k, x in cast(dict[str, Any], v).items():
                walk(x, f"{where}.{k}" if where else str(k), depth + 1)

    walk(body, "", 0)
    out: dict[str, Any] = {"path": path, "lists": []}
    facts: list[Fact] = []
    for where, items in lists:
        entry: dict[str, Any] = {"items": where, "count": len(items), "fields": {}, "samples": []}
        facts.append(Fact(id="", query=Query(endpoint=name, items=where), value=len(items)))
        rows = [cast(dict[str, Any], i) for i in items if isinstance(i, dict)]
        keys = list(dict.fromkeys(k for r in rows for k in r))
        for k in keys[:40]:
            values: list[Any] = [r.get(k) for r in rows if not isinstance(r.get(k), (dict, list))]
            distinct = {json.dumps(v, sort_keys=True) for v in values}
            spread: dict[str, Any] = {"distinct": len(distinct)}
            if 0 < len(distinct) <= SPREAD:
                counts: dict[str, int] = {}
                for v in values:
                    counts[json.dumps(v, ensure_ascii=False)] = counts.get(json.dumps(v, ensure_ascii=False), 0) + 1
                spread["values"] = counts
                for v in {json.dumps(v, sort_keys=True): v for v in values}.values():
                    q = Query(endpoint=name, items=where, where=[Cond(field=k, equals=v)])
                    facts.append(Fact(id="", query=q, value=sum(1 for r in rows if r.get(k) == v)))
            entry["fields"][k] = spread
        entry["samples"] = [_short(r) for r in items[:SAMPLES]]
        out["lists"].append(entry)
    if not lists:
        out["value"] = _short(body)
    return out, facts


def _short(v: Any, depth: int = 0) -> Any:
    if isinstance(v, str):
        return v if len(v) <= 120 else v[:120] + "..."
    if isinstance(v, list):
        return f"[{len(cast(list[Any], v))} items]"
    if isinstance(v, dict):
        d = cast(dict[str, Any], v)
        if depth >= 2:
            return f"{{{len(d)} fields}}"
        return {str(k): _short(x, depth + 1) for k, x in list(d.items())[:30]}
    return v


def cited(c: Cite, files: Mapping[str, Path], where: str) -> list[str]:
    """A citation names a file the agent was given and quotes it exactly."""
    path = files.get(c.file)
    if path is None:
        return [f"{where}: {c.file} is not a file of the snapshot; cite one of {', '.join(sorted(files))}"]
    if c.match not in path.read_text(errors="replace"):
        return [f"{where}: {c.match[:80]!r} does not appear in {c.file}; copy the words exactly"]
    return []


def loose_numbers(text: str, quoted: Sequence[str], where: str) -> list[str]:
    """Digits written in prose: a number goes in a count, unless it is a date, an id, or inside quoted words."""
    rest = text
    for q in sorted(quoted, key=len, reverse=True):
        if q:
            rest = rest.replace(q, " ")
    rest = ID.sub(" ", DATE.sub(" ", rest))
    found = re.findall(r"\d+(?:[.,]\d+)?", rest)
    return [f"{where}: {', '.join(found[:5])} written in prose; put a number in a count that names its query, "
            "so Jaynes can compute it"] if found else []


def counted(c: Count, raw: Mapping[str, Any], where: str) -> list[str]:
    """A count matches what its query computes on the raw responses; an empty one is told the number."""
    try:
        value = compute(c.query, raw)
    except (KeyError, ValueError, IndexError, TypeError) as err:
        return [f"{where}: the query cannot be computed: {err}"]
    if c.value is None:
        return [f"{where}: Jaynes computed {value} for this query; write it as the value"]
    if c.value != value:
        return [f"{where}: the value is {c.value}, but the query computes {value}"]
    return []


def raw_responses(files: Mapping[str, Path]) -> dict[str, Any]:
    """The raw responses a snapshot kept for checks, by summary file."""
    out: dict[str, Any] = {}
    for rel, path in files.items():
        if rel.startswith("private/api/"):
            try:
                out[rel.removeprefix("private/")] = json.loads(path.read_text())
            except ValueError:
                continue
    return out


def visible(files: Mapping[str, Path]) -> dict[str, Path]:
    """The snapshot files agents are given, by the name they cite them with."""
    return {rel: p for rel, p in files.items() if not rel.startswith("private/")}


def normal(text: str) -> str:
    """A name as compared: no accents, lower case, single spaces."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# --- serving a copy ---------------------------------------------------------------

def preview_spec(started: Mapping[str, Any]) -> dict[str, Any]:
    spec = started.get("preview")
    if not spec:
        raise Blocked("this role looks at the running product: start the run with --preview, the command that "
                      "serves the project on {port}, and --preview-path, the screen")
    return dict(spec)


@contextmanager
def serve(project: Path, started: Mapping[str, Any], source: Path | None,
          hidden: tuple[Path, ...] = ()) -> Generator[str]:
    """Serve a copy of ``project`` with the run's preview command and frozen data; yields its origin.

    A preview that does not start blocks the run: the person's command or data are wrong, not the agent.
    """
    spec = preview_spec(started)
    work = Path(tempfile.mkdtemp(prefix="jaynes-serve-")).resolve()
    folder, tmp = work / "project", work / "tmp"
    tmp.mkdir()
    shutil.copytree(project, folder, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    inputs.place(started, folder)
    if source is not None:
        link_dependencies(source, folder)
    port = free_port()
    log = work / "preview.log"
    proc: subprocess.Popen[bytes] | None = None
    try:
        try:
            argv = sandbox.command(str(spec["command"]).replace("{port}", str(port)),
                                   sandbox.Box((folder, tmp), hidden=hidden,
                                               ports=tuple(int(p) for p in spec.get("ports", []))))
        except sandbox.Unavailable as err:
            raise Blocked(str(err)) from None
        with log.open("ab") as out:
            proc = subprocess.Popen(argv, cwd=folder, env={**os.environ, **sandbox.environment(tmp)},
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        deadline = time.monotonic() + READY_WITHIN
        while True:
            if proc.poll() is not None:
                raise Blocked(f"the preview command exited with {proc.returncode}:\n" + _tail(log))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise Blocked(f"nothing answered on port {port} after {READY_WITHIN:.0f} s:\n" + _tail(log)) \
                        from None
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
        shutil.rmtree(work, ignore_errors=True)


def screen_path(started: Mapping[str, Any]) -> str:
    path = str(preview_spec(started).get("path") or "/")
    return path if path.startswith("/") else "/" + path


def theme(started: Mapping[str, Any]) -> str:
    return inputs.value(started, "theme") or "dark"


def other(theme_name: str) -> str:
    return "light" if theme_name == "dark" else "dark"


def denied(started: Mapping[str, Any]) -> list[str]:
    """GETs with effects the person listed, as paths."""
    return ["/" + d.strip().lstrip("/") for d in re.split(r"[,\s]+", inputs.value(started, "blocked_gets")) if d.strip()]


def run_path(origin: str, started: Mapping[str, Any], path: Sequence[Mapping[str, Any]]) -> list[str]:
    """Perform a path in Jaynes's Chrome, in the owner's theme; returns what went wrong, step by step."""
    from jaynes.browser import BrowserError, open_page
    try:
        with open_page(origin, deny=denied(started)) as page:
            page.view(theme(started))
            return [f"step {i} ({a.get('action')}): {err}" for i, a in enumerate(path, 1) if (err := page.act(a))]
    except BrowserError as err:
        raise Blocked(f"Jaynes's Chrome could not look at the product: {err}") from None


def checked_path(needs: Witnesses, path: Sequence[Mapping[str, Any]]) -> list[str]:
    """Serve the step's copy of the project and perform the path on it."""
    assert needs.project is not None
    with serve(needs.project, needs.started, needs.source, needs.hidden) as origin:
        return run_path(origin, needs.started, path)


def _tail(log: Path, size: int = 2000) -> str:
    try:
        return log.read_bytes()[-size:].decode(errors="replace")
    except OSError:
        return ""


def pass_or_fail(reasons: list[str]) -> Pass | Fail:
    reasons = list(dict.fromkeys(reasons))
    return Fail(*reasons[:30]) if reasons else Pass()
