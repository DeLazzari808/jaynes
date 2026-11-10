"""A request becomes a plan of runs, one per role: the delivery of one enters the next, and runs that do not depend
on each other run at the same time.

Whoever starts the request writes its plan: today the agent in Claude Code, later a direction specialist. Jaynes
checks the plan, records it with the request in the person's words, and starts each run once every run it follows
has delivered: merged, on its branch, or with its outputs when it has no project. It never waits for samples, which
come later and correct on top. A later run works from where the earlier delivery landed, the person's branch once
it merged or the earlier run's branch when it did not, and its task names what the earlier runs delivered. The run
that delivers starts the next ones, and the person's page looks again every few seconds, so a plan goes on after a
crash.
"""

from __future__ import annotations

import fcntl
import importlib
import json
import re
import sys
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from jaynes.conductor import Conductor, RunState, new_run_id, project, start_in_background
from jaynes.role import Role

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
MOST = 20  # runs in one plan
SHOWN = 4000  # characters of an earlier run's outputs a later run's task carries, when it has no branch
GRACE = 60.0  # seconds a run marked as started has to record its start before it is started again


class PlanError(ValueError):
    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


@dataclass(frozen=True)
class Planned:
    """One run of a plan: its name in the plan, the role's reference, its task and the runs it follows."""

    name: str
    role: str
    task: str
    run: str  # the run's id, given when the plan is recorded
    after: tuple[str, ...] = ()
    preview: str = ""
    preview_path: str = ""
    preview_ports: str = ""
    suite: str = ""
    inputs: dict[str, str] = field(default_factory=dict[str, str])
    runtimes: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class Request:
    id: str
    text: str  # what the person asked, in their words
    project: str | None  # the folder every run works on, if any
    runs: tuple[Planned, ...]
    at: float

    def named(self, name: str) -> Planned:
        return next(p for p in self.runs if p.name == name)


def ref(name: str) -> str:
    """A role's reference as runs record it: a built-in's module:attribute, or a role file's absolute path."""
    from jaynes.roles import BUILT_IN
    if name.endswith(".toml"):
        return str(Path(name).resolve())
    if ":" in name:
        return name
    if name not in BUILT_IN:
        raise PlanError([f"no built-in role {name!r}: use one of {', '.join(BUILT_IN)}, a role file (.toml), or "
                         "module:attribute"])
    return BUILT_IN[name]


def load(reference: str) -> Role:
    from jaynes.role_file import RoleFileError
    from jaynes.role_file import load as load_file
    try:
        if reference.endswith(".toml"):
            found: object = load_file(Path(reference))
        else:
            module, _, attr = reference.partition(":")
            if str(Path.cwd()) not in sys.path:  # a role in the current folder, as jaynes run finds it
                sys.path.insert(0, str(Path.cwd()))
            found = getattr(importlib.import_module(module), attr)
    except (RoleFileError, ImportError, AttributeError, ValueError) as err:
        raise PlanError([f"the role {reference} does not load: {err}"]) from None
    if not isinstance(found, Role):
        raise PlanError([f"{reference} is not a role"])
    return found


def _text(x: Mapping[str, object], key: str) -> str:
    value = x.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _pairs(x: Mapping[str, object], key: str, where: str) -> dict[str, str]:
    value: object = x.get(key) or {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                              for k, v in cast(dict[object, object], value).items()):
        raise PlanError([f"{where}: {key} must map names to text"])
    return dict(cast(dict[str, str], value))


def parse(data: object, has_project: bool) -> tuple[str, list[Planned]]:
    """The request and its runs from a plan, or every reason it is refused. A run follows only runs listed before
    it, so a plan never loops, and it may follow at most one run with a project that does not merge, since it
    works from one branch."""
    if not isinstance(data, dict):
        raise PlanError(['a plan is an object with "request" and "runs"'])
    d = cast(dict[str, object], data)
    text, raw = _text(d, "request"), d.get("runs")
    reasons: list[str] = [] if text else ['"request" must say what the person asked, in their words']
    if not isinstance(raw, list) or not raw:
        raise PlanError([*reasons, '"runs" must list at least one run'])
    items = cast(list[object], raw)
    if len(items) > MOST:
        raise PlanError([*reasons, f"a plan has at most {MOST} runs"])
    runs: list[Planned] = []
    branches: set[str] = set()  # runs with a project that do not merge: a later run works from their branch
    for i, item in enumerate(items, 1):
        where = f"run {i}"
        if not isinstance(item, dict):
            reasons.append(f"{where} must be an object")
            continue
        x = cast(dict[str, object], item)
        name = _text(x, "name")
        if not NAME.fullmatch(name):
            reasons.append(f"{where}: name must be lowercase letters, digits and dashes, such as identity")
            continue
        where = f"run {name}"
        if any(p.name == name for p in runs):
            reasons.append(f"{where} is listed twice")
            continue
        after_raw: object = x.get("after") or []
        after = [a for a in cast(list[object], after_raw) if isinstance(a, str)] if isinstance(after_raw, list) else []
        if not isinstance(after_raw, list) or len(after) != len(cast(list[object], after_raw)):
            reasons.append(f"{where}: after must list names")
        known = {p.name for p in runs}
        if unknown := [a for a in after if a not in known]:
            reasons.append(f"{where} follows {', '.join(unknown)}, which must be listed before it")
        if has_project and len([a for a in after if a in branches]) > 1:
            reasons.append(f"{where} follows more than one run that does not merge; it can work from one branch only")
        task = _text(x, "task")
        if not task:
            reasons.append(f"{where}: task must say what this run does")
        try:
            reference = ref(_text(x, "role"))
            role = load(reference)
            from jaynes import verify
            if problems := verify.verify(role):
                reasons.append(f"{where}: {verify.report(role, problems)}")
            if has_project and not role.merges:
                branches.add(name)
            runs.append(Planned(name, reference, task, new_run_id(), tuple(dict.fromkeys(after)),
                                _text(x, "preview"), _text(x, "preview_path"), _text(x, "preview_ports"),
                                _text(x, "suite"), _pairs(x, "inputs", where), _pairs(x, "runtimes", where)))
        except PlanError as err:
            reasons.extend(f"{where}: {r}" for r in err.reasons)
    if reasons:
        raise PlanError(reasons)
    return text, runs


def new_request_id() -> str:
    return "q" + new_run_id()[1:]


def record(c: Conductor, text: str, runs: Sequence[Planned], folder: Path | None) -> Request:
    """Record the request with its plan; nothing starts yet."""
    rid = new_request_id()
    where = None
    if folder is not None:
        from jaynes.project import resolve
        where = str(resolve(folder)[0])
    c.store.append("RequestPlanned", {"request": text, "project": where, "runs": [asdict(p) for p in runs]},
                   run_id=rid)
    return requests(c, rid)[0]


def _request(e: Any) -> Request:
    p = e.payload
    runs = tuple(Planned(str(r["name"]), str(r["role"]), str(r["task"]), str(r["run"]), tuple(r.get("after") or ()),
                         str(r.get("preview", "")), str(r.get("preview_path", "")), str(r.get("preview_ports", "")),
                         str(r.get("suite", "")), dict(r.get("inputs") or {}), dict(r.get("runtimes") or {}))
                 for r in cast(list[dict[str, Any]], p["runs"]))
    return Request(str(e.run_id), str(p["request"]), p.get("project"), runs, float(e.ts))


def requests(c: Conductor, rid: str | None = None) -> list[Request]:
    """Every request with its plan, oldest first, or only ``rid``."""
    return [_request(e) for e in c.store.events("RequestPlanned", run_id=rid)]


def of_run(c: Conductor, run_id: str) -> tuple[Request, Planned] | None:
    """The request a run belongs to, and its place in the plan."""
    first = next(iter(c.store.events("RunStarted", run_id=run_id)), None)
    mark = first.payload.get("request") if first is not None else None
    if not isinstance(mark, dict):
        return None
    m = cast(dict[str, Any], mark)
    found = requests(c, str(m.get("id")))
    if not found:
        return None
    return found[0], found[0].named(str(m.get("name")))


def _state(c: Conductor, run_id: str) -> RunState | None:
    events = list(c.store.events(run_id=run_id))
    return project(events) if events else None


def delivered(role: Role, st: RunState) -> bool:
    """Whether the run delivered what a later run works from: merged, on its branch when it does not merge or
    could not, or every step done when it has no project. Samples still open do not matter."""
    if st.started is None or st.outcome in ("dismissed", "ended") or st.undone is not None:
        return False
    if st.finished:
        return True
    if st.branch is not None:
        return not role.merges or bool(st.merged) or st.not_merged is not None
    if (st.started or {}).get("project"):
        return False
    return all(st.step(s.name).checked is not None for s in role.steps)


def stopped(st: RunState) -> str | None:
    """Why a run will never deliver, if it will not."""
    if st.outcome == "dismissed":
        return "it was dropped"
    if st.outcome == "ended":
        return "its output ended it"
    if st.undone is not None:
        return "its merge was undone"
    return None


@dataclass(frozen=True)
class Place:
    """Where one run of a plan is."""

    planned: Planned
    state: str  # planned, working, delivered or stopped
    why: str = ""  # for planned, what it waits for; for stopped, why


def places(c: Conductor, req: Request) -> list[Place]:
    out: list[Place] = []
    by: dict[str, Place] = {}
    for p in req.runs:
        st = _state(c, p.run)
        if st is not None and st.started is not None:
            why = stopped(st)
            place = (Place(p, "stopped", why) if why else
                     Place(p, "delivered") if delivered(load(p.role), st) else Place(p, "working"))
        elif any(by[a].state == "stopped" for a in p.after):
            names = [a for a in p.after if by[a].state == "stopped"]
            place = Place(p, "stopped", f"{', '.join(names)} will not deliver")
        elif (mark := started_mark(c, req, p.name)) is not None and c.clock() - mark < GRACE:
            place = Place(p, "working", "starting")
        else:
            waits = [a for a in p.after if by[a].state != "delivered"]
            place = Place(p, "planned", f"after {', '.join(waits)}" if waits else "starting")
        by[p.name] = place
        out.append(place)
    return out


def started_mark(c: Conductor, req: Request, name: str) -> float | None:
    """When the run was last started for the plan; a process that died before the run recorded its start leaves
    only this."""
    marks = [e.ts for e in c.store.events("RequestRunStarted", run_id=req.id) if e.payload.get("name") == name]
    return marks[-1] if marks else None


def status(c: Conductor, req: Request) -> str:
    """delivered once every run delivered, stopped when one never will, otherwise working."""
    states = {x.state for x in places(c, req)}
    return "delivered" if states == {"delivered"} else "stopped" if "stopped" in states else "working"


def _files(c: Conductor, st: RunState) -> list[str]:
    files: set[str] = set()
    for _, h in (st.branch or {}).get("patches", []):
        path = c.home / "objects" / f"{h}.patch"
        if path.is_file():
            files |= {line.split(" b/", 1)[1] for line in path.read_text(errors="replace").splitlines()
                      if line.startswith("diff --git ") and " b/" in line}
    return sorted(files)


def _outputs(c: Conductor, role: Role, st: RunState) -> str:
    out: dict[str, object] = {}
    for s in role.steps:
        h = (st.step(s.name).checked or {}).get("output_hash")
        if h and (path := c.home / "objects" / f"{h}.json").is_file():
            out[s.name] = json.loads(path.read_text())
    shown = json.dumps(out, ensure_ascii=False)
    return shown if len(shown) <= SHOWN else shown[:SHOWN] + " [cut]"


def handed(c: Conductor, req: Request, p: Planned) -> tuple[str, str | None]:
    """The task a run starts with, naming what the runs it follows delivered, and the commit it works from: None
    for the project's last one, as when every run it follows merged."""
    lines: list[str] = []
    base: str | None = None
    for name in p.after:
        before = req.named(name)
        st = _state(c, before.run)
        if st is None or st.started is None:
            continue
        role = load(before.role)
        line = before.task.splitlines()[0][:200].rstrip()
        said = f"- {name} ({role.name}, run {before.run}): {line}{'' if line.endswith(('.', '!', '?')) else '.'}"
        if st.merged and st.undone is None:
            said += f" Merged into {st.merged[-1]['into']}, which this run works from."
        elif st.branch is not None:
            base = str(st.branch["commit"])
            said += f" On the branch {st.branch['branch']}, which this run works from."
        else:
            said += f" Its outputs: {_outputs(c, role, st)}"
        if files := _files(c, st):
            said += f" Files it changed: {', '.join(files)}."
        lines.append(said)
    task = p.task
    if lines:
        task += ("\n\nThis run is part of a request, in the person's words: " + json.dumps(req.text, ensure_ascii=False)
                 + "\nWhat the runs before it delivered:\n" + "\n".join(lines))
    elif len(req.runs) > 1:
        task += "\n\nThis run is part of a request, in the person's words: " + json.dumps(req.text, ensure_ascii=False)
    return task, base


Start = Callable[[Conductor, Request, Planned, str, str | None], None]


def start_run(c: Conductor, req: Request, p: Planned, task: str, base: str | None) -> None:
    """Start the run as ``jaynes run`` does, in the background."""
    extra = [arg for flag, value in (("--request", f"{req.id}:{p.name}"), ("--base", base)) if value
             for arg in (flag, value)]
    start_in_background(c.home, p.run, p.role, task, p.runtimes, project=req.project or "", preview=p.preview,
                        preview_path=p.preview_path, preview_ports=p.preview_ports, suite=p.suite, inputs=p.inputs,
                        extra=extra)


@contextmanager
def _locked(c: Conductor, rid: str) -> Generator[bool]:
    """One process at a time decides what starts; another one finds it taken and leaves it to that one."""
    path = c.home / "runs" / f"{rid}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def advance(c: Conductor, rid: str, start: Start | None = None) -> list[str]:
    """Start every run of the request whose earlier runs all delivered; the names started."""
    begun: list[str] = []
    with _locked(c, rid) as mine:
        if not mine:
            return begun
        [req] = requests(c, rid)
        for place in places(c, req):
            if place.state != "planned" or place.why != "starting":
                continue
            task, base = handed(c, req, place.planned)
            c.store.append("RequestRunStarted", {"name": place.planned.name, "run": place.planned.run}, run_id=rid)
            (start or start_run)(c, req, place.planned, task, base)
            begun.append(place.planned.name)
    return begun


def advance_all(c: Conductor, start: Start | None = None) -> None:
    """Advance every request still working: what the person's page does every few seconds."""
    for req in requests(c):
        if status(c, req) == "working":
            advance(c, req.id, start)
