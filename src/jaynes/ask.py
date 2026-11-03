"""A stop asked in the terminal: the input of Claude Code's question dialog, and the answer its hook hands back.

``jaynes ask <run>`` prints the dialog's input for the run's open stop, and the agent passes it to
AskUserQuestion unchanged. After the person answers, Claude Code runs ``jaynes answered`` with the dialog's input
and the answer. The hook renders the stop again and records the answer only when the dialog showed exactly that,
so an agent that changes the question, its evidence or its options gets nothing recorded.

Accept, with any note the person adds to it, and return, with the note that says what is wrong, are answered
here. Typing an answer of one's own is a return with that text as the note. Change and context, which need the
output edited or a step chosen, stay in the review page. A step that says how its stop shows the output is shown
here in its sentences, with a word that the pictures are in the review page; the output itself stays there. At a stop that lets the person choose among the output's
items, typing their numbers or ids in order, such as ``2, 1``, chooses them; the dialog has room for four
options and no order, so the items are listed in the evidence rather than as options.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from jaynes import project as repo
from jaynes.conductor import AnswerRejected, Conductor, RunBusy, RunState, StepState, project, return_to
from jaynes.role import Role, Step

HEADER = "Jaynes"
ACCEPT, RETURN = "Accept", "Return"
LIMIT = 6000  # of the evidence shown in the dialog; the rest is named, never dropped silently
MARK = re.compile(r"\[run (\S+), step (\S+)\]$")


def dialog(c: Conductor, role: Role, run_id: str) -> dict[str, Any] | None:
    """AskUserQuestion's input for the open stop of ``run_id``, or None when no stop waits there."""
    w = next((x for x in c.queue() if x.run_id == run_id and x.kind == "stop"), None)
    if w is None:
        return None
    st = project(c.store.events(run_id=run_id))
    s = st.step(w.step)
    target = next(x for x in role.steps if x.name == w.step)
    pure = target.output is None
    needs = [n.name for n in target.needs]
    question = w.question or (f"Do you accept the result of {', '.join(needs)}?" if pure and needs
                              else f"Is the output of {w.step} right?")
    ends = bool(s.open_stop and s.open_stop[1].get("ends"))
    shown = cast(list[dict[str, Any]] | None, s.open_stop[1].get("shown")) if s.open_stop else None
    evidence = _evidence(c, role, st, s, target, shown)
    accept = "Accepting ends the run here, with nothing delivered." if ends else "It is right: the run goes on."
    back = return_to(role, w.step) if pure else w.step
    items = _items(c, s, target)
    if items:
        evidence = "Choose by typing numbers in order, such as 2, 1:\n" + "\n".join(
            f"{n}. {i}: {label}" for n, (i, label) in enumerate(items, 1)) + "\n\n" + evidence
        question += " Type the numbers you choose, most important first; Accept keeps them all in this order."
        accept = "All of them are right, in the order shown: the run goes on."
    if shown:  # Claude Code hides a preview it cannot show in full; the question always shows
        question += "\n\n" + _sentences(shown, chosen_apart=target.choose is not None, front_only=True)
    return {"questions": [{
        "question": f"{question}\n\n[run {run_id}, step {w.step}]",
        "header": HEADER,
        "multiSelect": False,
        "options": [
            {"label": ACCEPT, "description": accept, "preview": evidence},
            {"label": RETURN, "description": f"Not right: add a note saying what is wrong, and {back} runs again "
                                             "with it.", "preview": evidence},
        ],
    }]}


def _evidence(c: Conductor, role: Role, st: RunState, s: StepState, target: Step[Any],
              shown: list[dict[str, Any]] | None = None) -> str:
    parts: list[str] = []
    if s.note:
        parts.append(f"You returned the last round with: {s.note}")
    later = set((s.checked or {}).get("floor") or [])
    patch = (s.checked or {}).get("patch")
    if later and patch:
        text = (c.home / "objects" / f"{patch}.patch").read_bytes().decode(errors="replace")
        diff = "".join(d for path, d in repo.diffs(text).items() if path in later)
        parts.append(f"These files run later, outside any box; read every line: {', '.join(sorted(later))}\n{diff}")
    if shown:
        parts.append(_sentences(shown, chosen_apart=target.choose is not None))
    elif target.output is None:
        for need in target.needs:
            parts.append(f"From {need.name}:\n{_text(_value(c, st.step(need.name), need))}")
    else:
        parts.append(f"Output:\n{_text(_value(c, s, target))}")
    for step, patch in c.patches(role, st):
        parts.append(f"Changes from {step}:\n{patch.decode(errors='replace')}")
    text = "\n\n".join(parts)
    if len(text) > LIMIT:
        text = (text[:LIMIT] + f"\n\n[{len(text) - LIMIT} more characters not shown: read them in the review page, "
                "jaynes review --open]")
    return text


def _sentences(shown: list[dict[str, Any]], chosen_apart: bool, front_only: bool = False) -> str:
    """What the stop shows, as lines: the items the person may choose are listed apart, numbered. ``front_only``
    keeps the lines in front, without what is folded below or the word about pictures."""
    def line(x: dict[str, Any]) -> str:
        return ": ".join(filter(None, [str(x["title"]), str(x.get("text") or "")]))
    main = [line(x) for x in shown if not x.get("more") and not (chosen_apart and x.get("id"))]
    if front_only:
        return "\n".join(main)
    more = [line(x) for x in shown if x.get("more")]
    pictures = sum(len(x.get("pictures") or []) for x in shown)
    out = "\n".join(main)
    out += "\n\nMore:\n" + "\n".join(more) if more else ""
    out += (f"\n\n{pictures} picture{'s' * (pictures > 1)} of this stop {'are' if pictures > 1 else 'is'} in the "
            "review page (jaynes review --open), with the full output under Details." if pictures else "")
    return out.strip()


def _items(c: Conductor, s: StepState, target: Step[Any]) -> list[tuple[str, str]]:
    if target.choose is None or target.output is None or s.checked is None:
        return []
    raw = (c.home / "objects" / f"{s.checked['output_hash']}.json").read_text()
    return [(str(i), str(label)) for i, label in target.choose.items(target.output.model_validate_json(raw))]


def chosen(text: str, items: list[tuple[str, str]]) -> list[str] | None:
    """The ids a typed answer chooses, by number or id, in order; None when it is not a choice."""
    words = [w for w in re.split(r"[\s,;]+", text.strip()) if w]
    ids = {i.lower(): i for i, _ in items}
    out: list[str] = []
    for w in words:
        if w.isdigit() and 1 <= int(w) <= len(items):
            out.append(items[int(w) - 1][0])
        elif w.lower() in ids:
            out.append(ids[w.lower()])
        else:
            return None
    return out or None


def _value(c: Conductor, s: StepState, target: Step[Any]) -> Any:
    if s.checked is None or s.checked.get("output_hash") is None:
        return None
    raw = (c.home / "objects" / f"{s.checked['output_hash']}.json").read_text()
    if target.output is not None:
        return target.output.model_validate_json(raw).model_dump(mode="json")
    return json.loads(raw)


def _text(value: Any) -> str:
    """Fields one per line, a string with lines as it is, anything else as JSON."""
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_text(v)}" if not isinstance(v, str) or "\n" not in v else f"{k}:\n{v}"
                         for k, v in cast(dict[str, Any], value).items())
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def answered(event: dict[str, Any], home: Path, role_of: Callable[[Conductor, str], Role]) -> str | None:
    """Record the person's answer to a Jaynes question in the dialog Claude Code just closed.

    Returns what to tell the agent, or None when the dialog asked nothing of Jaynes. ``role_of`` loads the role
    a run was started with.
    """
    if event.get("tool_name") != "AskUserQuestion":
        return None
    tool_input = cast(dict[str, Any], event.get("tool_input") or {})
    response = event.get("tool_response")
    response = cast(dict[str, Any], response if isinstance(response, dict) else {})
    answers = cast(dict[str, Any], response.get("answers") or tool_input.get("answers") or {})
    notes = cast(dict[str, Any], response.get("annotations") or tool_input.get("annotations") or {})
    said: list[str] = []
    for q in cast(list[dict[str, Any]], tool_input.get("questions") or []):
        text = str(q.get("question", ""))
        mark = MARK.search(text)
        if q.get("header") != HEADER or mark is None:
            continue
        run_id, step = mark.groups()
        said.append(_record(home, role_of, run_id, step, q, answers.get(text), notes.get(text)))
    return "\n".join(said) or None


def _record(home: Path, role_of: Callable[[Conductor, str], Role], run_id: str, step: str, shown: dict[str, Any],
            choice: Any, note_of: Any) -> str:
    c = Conductor(home)
    try:
        role = role_of(c, run_id)
    except (SystemExit, Exception) as err:  # a run Jaynes cannot load is never answered
        return f"Jaynes recorded nothing for run {run_id}: {err}"
    expected = dialog(c, role, run_id)
    want = expected["questions"][0] if expected else None
    if want is None or not _same(shown, want):
        return (f"Jaynes recorded nothing for run {run_id}: the question differs from the stop's. Run "
                f"`jaynes ask {run_id}` and pass its output to AskUserQuestion unchanged.")
    note = str(cast(dict[str, Any], note_of).get("notes") or "").strip() if isinstance(note_of, dict) else ""
    choice = str(choice or "").strip()
    target = next(x for x in role.steps if x.name == step)
    picked = chosen(choice, _items(c, project(c.store.events(run_id=run_id)).step(step), target)) \
        if choice not in (ACCEPT, RETURN) else None
    if picked:
        answer, text = "choose", note or None
    elif choice == ACCEPT:
        answer, text = "accept", note or None
    elif choice == RETURN:
        answer, text = "return", note
    elif choice:
        answer, text = "return", "\n".join(filter(None, [choice, note]))  # their own words say what is wrong
    else:
        return f"Jaynes recorded nothing for run {run_id}: the person gave no answer. Ask again."
    if answer == "return" and not text:
        return (f"Jaynes recorded nothing for run {run_id}: a return needs a note saying what is wrong. Ask "
                "again with `jaynes ask`; the person adds the note to Return, or types it as their own answer.")
    to = return_to(role, step) if answer == "return" and target.output is None else None
    opened = project(c.store.events(run_id=run_id)).step(step).open_stop
    evidence = opened[1].get("evidence") if opened else None
    try:
        c.answer(role, run_id, step, answer, note=text, to=to, by="person", via="terminal",
                 evidence=str(evidence) if evidence else None, chosen=picked)
    except AnswerRejected as err:
        return f"Jaynes recorded nothing for run {run_id}: " + "; ".join(err.reasons)
    except RunBusy:
        return f"Jaynes recorded nothing for run {run_id}: the run is busy in another process. Ask again."
    what = f"choose {', '.join(picked)}" if picked else answer
    return (f"Jaynes recorded the person's answer at {step}: {what}. Continue the run in the background with "
            f"`jaynes run {run_id}`.")


def _same(shown: dict[str, Any], want: dict[str, Any]) -> bool:
    """The dialog showed the question, its options and their evidence as Jaynes rendered them."""
    def key(q: dict[str, Any]) -> tuple[Any, ...]:
        options = cast(list[dict[str, Any]], q.get("options") or [])
        return (q.get("question"), q.get("header"), bool(q.get("multiSelect")),
                tuple((o.get("label"), o.get("description"), o.get("preview")) for o in options))
    return key(shown) == key(want)
