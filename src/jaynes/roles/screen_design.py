"""Design a screen from how its owner uses it: moments, objects, walkthrough and requirements, then one slice.

The method itself is the person's: it comes in as an input file, with their finishing heuristics, and this role keeps
only its structure and its checks. Jaynes looks at the product itself, with its own Chrome on a copy served in a box
with the data frozen at the start (``jaynes.screens``); agents see summaries and pictures, and every case, count and
capture they cite is checked against what Jaynes saw.

1. snapshot (Jaynes): the screen in the owner's theme, on desktop and phone, its text, controls, and a summary of
   each API response with the facts computed from it.
2. moments: up to five moments where the owner gets stuck, each with a real case quoted from the snapshot. The
   owner chooses two to five and orders them, with the screen in front of them.
3. object-map: the things the owner thinks in, where each exists, how many there are, what is missing, and the
   routes between them. Every object of the chosen moments is on it. It does not stop: the owner judges it in the
   requirements it leads to, not as data.
4. paths, then walkthrough (Jaynes): each moment click by click in a closed vocabulary, performed by Jaynes with
   everything but the product's own GETs refused, one capture per action.
5. breaks: where each moment breaks, citing captures of that moment.
6. requirements: one per break, in the order of the moments, with the first slice and where its moment must end.
   The owner approves them, each beside the capture where its break happens.
7. test, then implement: the slice's path with its ending, which must fail on the product as it is; then the change,
   which makes it pass with the suite, without touching tests.
8. measure (Jaynes): the person's own measuring and comparing tools; what fails goes back to implement.
9. proof (Jaynes) and review: where the slice ends, reached by its path on the product as it was and as changed,
   in both themes and sizes; the changed screen as it opens; and the slice's transitions. Defects found go back to
   implement.
10. report (Jaynes), accept: where the slice ends, before and after, on desktop and phone, cut around what changed, a line per change, and folded below
    them the measures, what was not verified, and tokens and minutes against the budget. Then heuristics: one entry for every note the owner gave and every defect only the proof found.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from jaynes import inputs, project as repo, screens as sc
from jaynes.browser import DESKTOP, PHONE, BrowserError, open_page
from jaynes.role import Back, Blocked, Choice, Fail, Input, Pass, Picture, Shown, StepContext, Witnesses, role, stop
from jaynes.roles import fix_bug as fb
from jaynes.roles import small_change as sm

TOOL_SECONDS = 600
EXPECTS = ("expectText", "expectRoute")


# --- outputs -----------------------------------------------------------------------

class Endpoint(BaseModel):
    path: str
    file: str = Field(description="its summary, among the snapshot's files")
    status: int
    bytes: int


class Snapshot(BaseModel):
    path: str
    theme: str
    url: str
    files: list[str] = Field(description="what agents may read and cite")
    endpoints: list[Endpoint]
    facts: list[sc.Fact] = Field(description="numbers Jaynes computed; a count may copy one's query")
    blocked: list[dict[str, str]] = Field(default_factory=list[dict[str, str]])
    regions: dict[str, list[int]] = Field(default_factory=dict[str, list[int]],
                                          description="where each screen picture has content: x, y, width and height")


class Case(BaseModel):
    text: str = Field(description="the real case, in a sentence")
    source: sc.Cite


class Moment(BaseModel):
    id: str = Field(pattern=r"^M[1-9]$")
    statement: str = Field(description="where the owner gets stuck or lost, in their words")
    case: Case
    objects: list[str] = Field(min_length=1, description="the things the owner thinks in during this moment")
    counts: list[sc.Count] = Field(default_factory=list[sc.Count])


class Moments(BaseModel):
    moments: list[Moment]


class Where(BaseModel):
    absent: bool = Field(default=False, description="true when it does not exist there yet")
    source: sc.Cite | None = None


class Link(BaseModel):
    to: str = Field(description="the id of the object it leads to")
    route: str = Field(description="how the interface takes the owner there, or should")


class Thing(BaseModel):
    id: str = Field(pattern=r"^O[1-9][0-9]?$")
    name: str
    aliases: list[str] = Field(default_factory=list[str])
    meaning: str = Field(description="what it is to the owner")
    screen: Where
    storage: Where
    count: sc.Count | None = None
    gap: str = Field(description="what is missing for it to be navigable")
    links: list[Link] = Field(default_factory=list[Link])


class ObjectMap(BaseModel):
    objects: list[Thing]


class Path_(BaseModel):
    moment: str
    steps: list[sc.WalkAction] = Field(min_length=1, max_length=15)


class Paths(BaseModel):
    paths: list[Path_]


class Capture(BaseModel):
    id: str
    moment: str
    step: int
    action: str
    url: str
    error: str
    controls: int
    filled: int = Field(description="filled buttons visible: primary actions")
    file: str
    text: str = Field(description="the capture's text and what Jaynes saw, beside the picture")
    region: list[int] = Field(default_factory=list[int], description="what the action changed in the picture: x, y, width and height")


class Walk(BaseModel):
    captures: list[Capture]
    blocked: list[dict[str, str]] = Field(default_factory=list[dict[str, str]])


class Break(BaseModel):
    id: str = Field(pattern=r"^B[1-9][0-9]?$")
    where: str = Field(description="the exact point the path breaks, and why")
    captures: list[str] = Field(min_length=1, description="ids of captures of this moment that show it")


class MomentBreaks(BaseModel):
    moment: str
    outcome: str = Field(pattern=r"^(break|no_break)$")
    reason: str = Field(default="", description="with no_break: why the path holds")
    breaks: list[Break] = Field(default_factory=list[Break])


class Breaks(BaseModel):
    moments: list[MomentBreaks]


class Requirement(BaseModel):
    id: str = Field(pattern=r"^R[1-9][0-9]?$")
    break_id: str
    objects: list[str] = Field(description="ids of the objects it concerns")
    route: str = Field(description="the route it adds or changes")
    need: str = Field(description="the requirement, in terms of objects and routes, not components")
    recommendation: str
    why: str


class Deferred(BaseModel):
    break_id: str
    reason: str


class End(BaseModel):
    route: str = Field(pattern=r"^/", description="the address where the moment ends, <name> for any one value")
    text: str = Field(min_length=1, description="text that shows the moment ended there")


class Slice(BaseModel):
    requirements: list[str] = Field(min_length=1)
    moment: str
    end: End


class Requirements(BaseModel):
    requirements: list[Requirement]
    deferred: list[Deferred] = Field(default_factory=list[Deferred])
    slice: Slice


class Test(BaseModel):
    moment: str
    path: list[sc.TestAction] = Field(min_length=1, max_length=20)
    failure: str = Field(description="a line Jaynes reports when it performs the path on the product as it is, "
                                     "copied exactly")


class Kept(BaseModel):
    id: str = Field(description="the id of a comparison failure")
    why: str


class Built(BaseModel):
    summary: str = Field(description="what changed and where to look, for the owner")
    changes: list[str] = Field(min_length=1, description="each change the owner will see on the screen, a line each")
    files: list[str]
    kept: list[Kept] = Field(default_factory=list[Kept], description="comparison failures kept on purpose, each "
                                                                    "with the reason")


class Failure(BaseModel):
    id: str
    what: str


class Measured(BaseModel):
    measured: bool
    compared: bool
    measure: list[Failure] = Field(default_factory=list[Failure])
    compare: list[Failure] = Field(default_factory=list[Failure])
    unexplained: list[Failure] = Field(default_factory=list[Failure])


class ProofShot(BaseModel):
    id: str
    file: str
    theme: str
    size: str
    kind: str = Field(description="screen; end-before or end-after, where the slice ends on the product as it was "
                                  "and as changed; or before, 90ms or settled around an action of the slice")
    region: list[int] = Field(default_factory=list[int], description="where to look in it: x, y, width and height")


class Proof(BaseModel):
    captures: list[ProofShot]
    errors: list[str] = Field(default_factory=list[str])


class Defect(BaseModel):
    id: str = Field(pattern=r"^D[1-9][0-9]?$")
    what: str
    capture: str = Field(description="the id of the proof capture that shows it")


class Review(BaseModel):
    defects: list[Defect]
    unverified: list[str] = Field(description="what you could not verify")


class StepCost(BaseModel):
    step: str
    turns: int
    tokens_new: int
    tokens_cached: int
    minutes: float


class Report(BaseModel):
    summary: str
    cost: list[StepCost]
    tokens_new: int
    tokens_cached: int
    minutes: float
    budget_tokens: int
    budget_minutes: float
    over: list[str]
    measure: list[Failure]
    compare: list[Failure]
    unexplained: list[Failure]
    defects: list[Defect]
    unverified: list[str]


class Said(BaseModel):
    id: str
    step: str
    kind: str = Field(description="return, change, context or accept with a note by the owner; defect found by "
                                  "the proof")
    text: str


class Notes(BaseModel):
    notes: list[Said]


class Entry(BaseModel):
    rule: str
    when: str = Field(description="where the rule applies")
    case: str = Field(description="the case in this run that supports it")
    covers: list[str] = Field(min_length=1, description="ids of the notes it answers")


class Heuristics(BaseModel):
    entries: list[Entry]


# --- trusted executors --------------------------------------------------------------

def _files(ctx: StepContext) -> Path:
    folder = ctx.workspace / "files"
    folder.mkdir(exist_ok=True)
    return folder


def take_snapshot(ctx: StepContext) -> Snapshot:
    """The screen as it is, served with the frozen data, and what the product's API answered."""
    assert ctx.needs.project is not None
    owner = sc.theme(ctx.started)
    if owner not in ("dark", "light"):
        raise Blocked(f"the theme input is dark or light, not {owner!r}")
    files, path = _files(ctx), sc.screen_path(ctx.started)
    (files / "api").mkdir()
    (files / "private" / "api").mkdir(parents=True)
    try:
        with sc.serve(ctx.needs.project, ctx.started, ctx.needs.source, ctx.needs.hidden) as origin, \
                open_page(origin, deny=sc.denied(ctx.started), record=True) as page:
            page.view(owner, DESKTOP)
            page.goto(path)
            shot = page.shot(files / "screen-desktop.png")
            page.view(owner, PHONE)
            page.goto(path)
            page.screenshot(files / "screen-phone.png")
            regions = {"screen-desktop.png": shot.region, "screen-phone.png": page.region()}
    except BrowserError as err:
        raise Blocked(f"Jaynes's Chrome could not look at the product: {err}") from None
    if not shot.text.strip():
        raise Blocked(f"the screen at {path} shows no text; check --preview-path and that the product reads its data")
    (files / "screen.txt").write_text(shot.text)
    (files / "controls.json").write_text(json.dumps(shot.controls, indent=1, ensure_ascii=False))
    endpoints: list[Endpoint] = []
    facts: list[sc.Fact] = []
    for where, r in sorted(page.responses.items()):
        body = str(r["body"])
        if len(body.encode()) > 10_000_000:
            continue
        try:
            data = json.loads(body)
        except ValueError:
            continue
        name = sc.summary_name(where)
        summary, found = sc.summarize(where, data)
        (files / name).write_text(json.dumps(summary, indent=1, ensure_ascii=False))
        (files / "private" / name).write_text(body)
        endpoints.append(Endpoint(path=where, file=name, status=int(r.get("status") or 0), bytes=len(body)))
        facts += found
    shown = sorted(p.relative_to(files).as_posix() for p in files.rglob("*") if p.is_file()
                   and not p.relative_to(files).as_posix().startswith("private/"))
    return Snapshot(path=path, theme=owner, url=shot.url, files=shown, endpoints=endpoints,
                    facts=[f.model_copy(update={"id": f"F{i}"}) for i, f in enumerate(facts[:sc.MAX_FACTS], 1)],
                    blocked=page.blocked, regions=regions)


def walk_paths(ctx: StepContext) -> Walk:
    """Every chosen moment's path on the product as it is, one capture per action."""
    assert ctx.needs.project is not None
    files, owner = _files(ctx), sc.theme(ctx.started)
    paths: Paths = ctx.needs[PATHS].value
    captures: list[Capture] = []
    blocked: list[dict[str, str]] = []
    try:
        with sc.serve(ctx.needs.project, ctx.started, ctx.needs.source, ctx.needs.hidden) as origin:
            for p in paths.paths:
                with open_page(origin, deny=sc.denied(ctx.started)) as page:
                    page.view(owner, DESKTOP)
                    for i, action in enumerate(p.steps, 1):
                        err = page.act(action.model_dump())
                        cid = f"{p.moment}-s{i:02}"
                        shot = page.shot(files / f"{cid}.png")
                        text = (f"capture {cid}\nurl: {shot.url}\naction: {json.dumps(action.model_dump())}\n"
                                f"error: {err or 'none'}\ncontrols visible: {len(shot.controls)}\n\n{shot.text[:6000]}")
                        (files / f"{cid}.txt").write_text(text)
                        captures.append(Capture(id=cid, moment=p.moment, step=i, action=action.action, url=shot.url,
                                                error=err, controls=len(shot.controls),
                                                filled=sum(1 for c in shot.controls if c.get("filled")),
                                                file=f"{cid}.png", text=f"{cid}.txt", region=shot.region))
                    blocked += page.blocked
    except BrowserError as err:
        raise Blocked(f"Jaynes's Chrome could not look at the product: {err}") from None
    return Walk(captures=captures, blocked=blocked)


def _tool(command: str, fills: dict[str, str], cwd: str) -> list[Failure]:
    """Run the person's measuring tool outside the box, from where they gave it; a tool that cannot measure blocks
    the run."""
    for key, value in fills.items():
        command = command.replace("{" + key + "}", value)
    try:
        done = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=TOOL_SECONDS, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise Blocked(f"{command} did not finish within {TOOL_SECONDS} s") from None
    if done.returncode != 0:
        raise Blocked(f"{command} could not measure (exit {done.returncode}):\n{done.stderr[-2000:]}")
    try:
        printed = json.loads(done.stdout)
        return [Failure(id=str(f["id"]), what=str(f["what"])) for f in printed["failures"]]
    except (ValueError, KeyError, TypeError):
        raise Blocked(f'{command} printed no {{"failures": [{{"id", "what"}}]}}:\n{done.stdout[-1000:]}') from None


def measure_slice(ctx: StepContext) -> Measured:
    """The person's measure on the changed screen, and compare against the version they last approved, on the same
    frozen data."""
    assert ctx.needs.project is not None
    measure, compare = inputs.value(ctx.started, "measure"), inputs.value(ctx.started, "compare")
    if not measure and not compare:
        return Measured(measured=False, compared=False)
    files, path, owner = _files(ctx), sc.screen_path(ctx.started), sc.theme(ctx.started)
    built: Built = ctx.needs[IMPLEMENT].value
    found: list[Failure] = []
    against: list[Failure] = []
    with sc.serve(ctx.needs.project, ctx.started, ctx.needs.source, ctx.needs.hidden) as origin:
        if measure:
            (files / "measure").mkdir()
            found = _tool(measure, {"url": origin + path, "theme": owner, "out": str(files / "measure")},
                          inputs.folder(ctx.started, "measure"))
        if compare:
            info: dict[str, Any] = dict(ctx.started.get("project") or {})
            approved = inputs.value(ctx.started, "approved") or str(info.get("base", ""))
            work = Path(tempfile.mkdtemp(prefix="jaynes-approved-"))
            try:
                try:
                    repo.prepare(Path(str(info["root"])), approved, [], work / "project", cache=work / "cache")
                except (repo.ProjectError, KeyError) as err:
                    raise Blocked(f"the approved version {approved} could not be copied: {err}") from None
                with sc.serve(work / "project", ctx.started, ctx.needs.source, ctx.needs.hidden) as before:
                    (files / "compare").mkdir()
                    against = _tool(compare, {"url": origin + path, "approved_url": before + path, "theme": owner,
                                              "out": str(files / "compare")}, inputs.folder(ctx.started, "compare"))
            finally:
                shutil.rmtree(work, ignore_errors=True)
    kept = {k.id for k in built.kept if k.why.strip()}
    return Measured(measured=bool(measure), compared=bool(compare), measure=found, compare=against,
                    unexplained=[f for f in against if f.id not in kept])


def _to_the_end(page: Any, path: list[sc.TestAction]) -> list[str]:
    """Perform the slice's path up to where it ends, without its expectations; what went wrong, by step."""
    return [f"step {i}: {err}" for i, a in enumerate(path, 1)
            if a.action not in EXPECTS and (err := page.act(a.model_dump()))]


def _the_end(ctx: StepContext, origin: str, files: Path, test: Test, kind: str) -> tuple[list[ProofShot], list[str]]:
    """Where the slice ends, reached by its path, in both themes on desktop and phone."""
    owner, shots, errors = sc.theme(ctx.started), list[ProofShot](), list[str]()
    for t in (owner, sc.other(owner)):
        for size, view in (("desktop", DESKTOP), ("phone", PHONE)):
            with open_page(origin, deny=sc.denied(ctx.started)) as page:
                page.view(t, view)
                errors += [f"{kind}, {t} {size}, {e}" for e in _to_the_end(page, test.path)]
                cid = f"E-{t}-{size}-{kind}"
                page.screenshot(files / f"{cid}.png")
                region = page.region()
                if kind == "after":  # cut around what changed, so the difference is in the picture
                    region = page.region(page.difference(files / f"E-{t}-{size}-before.png", files / f"{cid}.png")
                                         or region)
                shots.append(ProofShot(id=cid, file=f"{cid}.png", theme=t, size=size, kind=f"end-{kind}",
                                       region=region))
    return shots, errors


def prove(ctx: StepContext) -> Proof:
    """Where the slice ends, before and after the change, reached by the slice's path, in both themes and sizes;
    the changed screen as it opens; and the slice's path in the owner's theme with each action's transition:
    before, about 90 ms after, and settled."""
    assert ctx.needs.project is not None
    files, path, owner = _files(ctx), sc.screen_path(ctx.started), sc.theme(ctx.started)
    test: Test = ctx.needs[TEST].value
    end: End = ctx.needs[REQUIREMENTS].value.slice.end
    shots: list[ProofShot] = []
    errors: list[str] = []
    info: dict[str, Any] = dict(ctx.started.get("project") or {})
    work = Path(tempfile.mkdtemp(prefix="jaynes-before-"))
    try:
        try:  # the product as the run found it, for the before
            repo.prepare(Path(str(info["root"])), str(info["base"]), [], work / "project", cache=work / "cache")
        except (repo.ProjectError, KeyError) as err:
            raise Blocked(f"the product as it was could not be copied: {err}") from None
        with sc.serve(work / "project", ctx.started, ctx.needs.source, ctx.needs.hidden) as before:
            found, _ = _the_end(ctx, before, files, test, "before")  # the path may not get as far before the change
            shots += found
        with sc.serve(ctx.needs.project, ctx.started, ctx.needs.source, ctx.needs.hidden) as origin:
            found, missed = _the_end(ctx, origin, files, test, "after")
            shots += found
            errors += missed
            with open_page(origin, deny=sc.denied(ctx.started)) as page:
                for t in (owner, sc.other(owner)):
                    for size, view in (("desktop", DESKTOP), ("phone", PHONE)):
                        page.view(t, view)
                        page.goto(path)
                        cid = f"P-{t}-{size}"
                        page.screenshot(files / f"{cid}.png")
                        shots.append(ProofShot(id=cid, file=f"{cid}.png", theme=t, size=size, kind="screen",
                                               region=page.region()))
                page.view(owner, DESKTOP)
                for i, action in enumerate(test.path, 1):
                    a = action.model_dump()
                    if action.action in EXPECTS:
                        if err := page.act(a):
                            errors.append(f"step {i}: {err}")
                        continue
                    cid = f"T-s{i:02}"
                    page.screenshot(files / f"{cid}-before.png")
                    err = page.act(a, wait=False)
                    page.pump(0.09)
                    page.screenshot(files / f"{cid}-90ms.png")
                    page.settle()
                    page.screenshot(files / f"{cid}-settled.png")
                    if err:
                        errors.append(f"step {i}: {err}")
                    shots += [ProofShot(id=f"{cid}-{k}", file=f"{cid}-{k}.png", theme=owner, size="desktop", kind=k)
                              for k in ("before", "90ms", "settled")]
                for check in ({"action": "expectRoute", "route": end.route}, {"action": "expectText", "text": end.text}):
                    if err := page.act(check):
                        errors.append(f"the end: {err}")
    except BrowserError as err:
        raise Blocked(f"Jaynes's Chrome could not look at the product: {err}") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return Proof(captures=shots, errors=errors)


def report(ctx: StepContext) -> Report:
    """What the owner reads before accepting: the change, its measures, what was not verified, and its cost."""
    built: Built = ctx.needs[IMPLEMENT].value
    measured: Measured = ctx.needs[MEASURE].value
    review: Review = ctx.needs[REVIEW].value
    cost = [StepCost(step=c.step, turns=c.turns, tokens_new=c.tokens_new, tokens_cached=c.tokens_cached,
                     minutes=round(c.seconds / 60, 1)) for c in ctx.history.cost]
    new, cached = sum(c.tokens_new for c in cost), sum(c.tokens_cached for c in cost)
    minutes = round(sum(c.seconds for c in ctx.history.cost) / 60, 1)
    try:
        budget, budget_minutes = int(inputs.value(ctx.started, "budget") or 0), float(inputs.value(ctx.started, "minutes") or 0)
    except ValueError:
        raise Blocked("the budget input is a number of tokens, and minutes a number of minutes") from None
    over = ([f"{new} new tokens, over the budget of {budget}"] if budget and new > budget else []) + (
        [f"{minutes} minutes of agent turns, over the budget of {budget_minutes:g}"]
        if budget_minutes and minutes > budget_minutes else [])
    unverified = list(review.unverified) + (["not measured: no measure input"] if not measured.measured else []) + (
        ["not compared with the approved version: no compare input"] if not measured.compared else [])
    return Report(summary=built.summary, cost=cost, tokens_new=new, tokens_cached=cached, minutes=minutes,
                  budget_tokens=budget, budget_minutes=budget_minutes, over=over, measure=measured.measure,
                  compare=measured.compare, unexplained=measured.unexplained, defects=review.defects,
                  unverified=unverified)


def collect_notes(ctx: StepContext) -> Notes:
    """Every note the owner gave in the run and every defect the proof's review found, from the run's own record,
    so none can be left out."""
    out: list[Said] = []
    for n in ctx.history.notes:
        if n.by == "person" and n.answer in ("return", "change", "context", "accept", "choose"):
            text = n.note or f"changed the output of {n.step} without a note"
            out.append(Said(id=f"N{len([s for s in out if s.id.startswith('N')]) + 1}", step=n.step, kind=n.answer,
                            text=text))
    defects = [r for n in ctx.history.notes if n.by == "jaynes" and n.note == f"sent back from {REVIEW.name}"
               for r in n.reasons]
    final: Review = ctx.needs[REVIEW].value
    defects += [f"{d.id} ({d.capture}): {d.what}" for d in final.defects]
    for i, d in enumerate(dict.fromkeys(defects), 1):
        out.append(Said(id=f"P{i}", step=REVIEW.name, kind="defect", text=d))
    return Notes(notes=out)


# --- checks -----------------------------------------------------------------------

def _snapshot(needs: Witnesses) -> tuple[dict[str, Path], dict[str, Any]]:
    files = needs.files.get(SNAPSHOT.name, {})
    return sc.visible(files), sc.raw_responses(files)


def moments_cited(out: Moments, needs: Witnesses) -> Pass | Fail:
    """Two to five moments, each with a case quoted from the snapshot; numbers only in counts Jaynes computes."""
    shown, raw = _snapshot(needs)
    reasons = [] if 2 <= len(out.moments) <= 5 else [f"keep 2 to 5 moments, not {len(out.moments)}"]
    ids = [m.id for m in out.moments]
    reasons += [f"{i} appears twice" for i in dict.fromkeys(i for i in ids if ids.count(i) > 1)]
    for m in out.moments:
        reasons += sc.cited(m.case.source, shown, f"{m.id} case")
        quoted = [m.case.source.match]
        reasons += sc.loose_numbers(m.statement, quoted, f"{m.id} statement")
        reasons += sc.loose_numbers(m.case.text, quoted, f"{m.id} case")
        for j, c in enumerate(m.counts, 1):
            reasons += sc.counted(c, raw, f"{m.id} count {j}")
    return sc.pass_or_fail(reasons)


def objects_mapped(out: ObjectMap, needs: Witnesses) -> Pass | Fail:
    """Every object of the chosen moments is on the map; where it exists is quoted; counts are computed."""
    shown, raw = _snapshot(needs)
    chosen: Moments = needs[MOMENTS].value
    known = {sc.normal(x): o.id for o in out.objects for x in [o.id, o.name, *o.aliases]}
    reasons = [f"{name}, an object of {m.id}, is not on the map; add it, or name it as an alias"
               for m in chosen.moments for name in m.objects if sc.normal(name) not in known]
    ids = [o.id for o in out.objects]
    reasons += [f"{i} appears twice" for i in dict.fromkeys(i for i in ids if ids.count(i) > 1)]
    for o in out.objects:
        quoted: list[str] = []
        for side, where in (("screen", o.screen), ("storage", o.storage)):
            if where.source is not None:
                reasons += sc.cited(where.source, shown, f"{o.id} {side}")
                quoted.append(where.source.match)
            elif not where.absent:
                reasons.append(f"{o.id} {side}: say where it exists, quoting the snapshot, or mark it absent")
        if o.count is not None:
            reasons += sc.counted(o.count, raw, f"{o.id} count")
        reasons += [f"{o.id} links to {link.to}, which is not on the map" for link in o.links if link.to not in ids]
        reasons += sc.loose_numbers(o.meaning, quoted, f"{o.id} meaning") + sc.loose_numbers(o.gap, quoted, f"{o.id} gap")
    return sc.pass_or_fail(reasons)


def paths_cover(out: Paths, needs: Witnesses) -> Pass | Fail:
    """One path per chosen moment, each starting by opening a page of the product."""
    chosen = [m.id for m in needs[MOMENTS].value.moments]
    got = [p.moment for p in out.paths]
    reasons = [f"{m} has no path" for m in chosen if m not in got]
    reasons += [f"{m} is not a chosen moment; they are {', '.join(chosen)}" for m in got if m not in chosen]
    reasons += [f"{m} has two paths" for m in dict.fromkeys(m for m in got if got.count(m) > 1)]
    reasons += [f"{p.moment} must start with open" for p in out.paths if p.steps[0].action != "open"]
    return sc.pass_or_fail(reasons)


def breaks_cite_captures(out: Breaks, needs: Witnesses) -> Pass | Fail:
    """Every chosen moment says where it breaks, citing captures of that same moment, or why it holds."""
    chosen = [m.id for m in needs[MOMENTS].value.moments]
    by_capture = {c.id: c.moment for c in needs[WALK].value.captures}
    got = [m.moment for m in out.moments]
    reasons = [f"{m}: say where it breaks, or no_break with the reason" for m in chosen if m not in got]
    reasons += [f"{m} is not a chosen moment" for m in got if m not in chosen]
    ids = [b.id for m in out.moments for b in m.breaks]
    reasons += [f"{i} appears twice" for i in dict.fromkeys(i for i in ids if ids.count(i) > 1)]
    for m in out.moments:
        if m.outcome == "break" and not m.breaks:
            reasons.append(f"{m.moment}: a break names where")
        if m.outcome == "no_break" and (m.breaks or not m.reason.strip()):
            reasons.append(f"{m.moment}: no_break lists no breaks and says why the path holds")
        for b in m.breaks:
            for c in b.captures:
                if c not in by_capture:
                    reasons.append(f"{b.id} cites {c}, which is not a capture Jaynes took")
                elif by_capture[c] != m.moment:
                    reasons.append(f"{b.id} cites {c}, a capture of {by_capture[c]}, not of {m.moment}")
    return sc.pass_or_fail(reasons)


def requirements_follow(out: Requirements, needs: Witnesses) -> Pass | Fail:
    """One requirement per break, every break covered or deferred, in the order of the moments; a slice of them."""
    order = {m.id: i for i, m in enumerate(needs[MOMENTS].value.moments)}
    breaks: Breaks = needs[BREAKS].value
    rank = {b.id: order.get(m.moment, 99) for m in breaks.moments for b in m.breaks}
    objects = {o.id for o in needs[OBJECTS].value.objects}
    got = [r.break_id for r in out.requirements]
    reasons = [f"{r.id} cites {r.break_id}, which is not a break" for r in out.requirements if r.break_id not in rank]
    reasons += [f"{b} has more than one requirement" for b in dict.fromkeys(b for b in got if got.count(b) > 1)]
    deferred = [d.break_id for d in out.deferred]
    reasons += [f"deferring {d}, which is not a break" for d in deferred if d not in rank]
    reasons += [f"{b} is covered and deferred" for b in deferred if b in got]
    reasons += [f"{b} has no requirement and is not deferred with a reason" for b in rank
                if b not in got and b not in deferred]
    ranks = [rank[r.break_id] for r in out.requirements if r.break_id in rank]
    if ranks != sorted(ranks):
        reasons.append("the requirements are not in the order of the chosen moments")
    reasons += [f"{r.id} names {o}, which is not on the object map" for r in out.requirements for o in r.objects
                if o not in objects]
    ids = {r.id for r in out.requirements}
    reasons += [f"the slice names {r}, which is not a requirement" for r in out.slice.requirements if r not in ids]
    if out.slice.moment not in order:
        reasons.append(f"the slice's moment {out.slice.moment} is not a chosen moment")
    return sc.pass_or_fail(reasons)


def _start(needs: Witnesses, moment: str) -> str:
    walked: Paths = needs[PATHS].value
    first = next((p.steps[0] for p in walked.paths if p.moment == moment), None)
    return str(getattr(first, "to", ""))


def _with_end(path: list[Any], end: End) -> list[dict[str, Any]]:
    return [a.model_dump() for a in path] + [{"action": "expectRoute", "route": end.route},
                                            {"action": "expectText", "text": end.text}]


def fails_first(out: Test, needs: Witnesses) -> Pass | Fail:
    """The slice's path starts where its moment's walkthrough started, and on the product as it is it does not reach
    the approved ending: Jaynes performs it and requires the failure the agent quotes."""
    s: Slice = needs[REQUIREMENTS].value.slice
    if out.moment != s.moment:
        return Fail(f"the test follows {out.moment}, but the slice is {s.moment}")
    start = _start(needs, s.moment)
    if out.path[0].action != "open" or getattr(out.path[0], "to", None) != start:
        return Fail(f"start the path where the walkthrough of {s.moment} started: open {start}")
    errors = sc.checked_path(needs, _with_end(out.path, s.end))
    if not errors:
        return Fail("the path already reaches the ending on the product as it is; it must fail before the change")
    if not out.failure.strip() or out.failure.strip() not in "\n".join(errors):
        return Fail(f"{out.failure.strip()!r} is not what Jaynes reported; copy a line of it exactly:\n"
                    + "\n".join(errors))
    return Pass()


def slice_works(out: Built, needs: Witnesses) -> Pass | Fail:
    """On the changed product the slice's path reaches its ending, and the project's suite passes."""
    test: Test = needs[TEST].value
    errors = sc.checked_path(needs, _with_end(test.path, needs[REQUIREMENTS].value.slice.end))
    if errors:
        return Fail("the slice's path does not reach its ending on the changed product:", *errors)
    command = needs[RUNNER].value.command
    if command:
        ran = fb._ran(needs, command)  # pyright: ignore[reportPrivateUsage]
        if isinstance(ran, Fail):
            return ran
        if ran.code != 0:
            return Fail(f"the suite fails: {command} exited with {ran.code}. The end of what it printed:\n{ran.output}")
    return Pass()


def defects_cite_proof(out: Review, needs: Witnesses) -> Pass | Fail:
    """Every defect cites a capture of the proof."""
    taken = {c.id for c in needs[PROOF].value.captures}
    reasons = [f"{d.id} cites {d.capture}, which is not a proof capture" for d in out.defects if d.capture not in taken]
    ids = [d.id for d in out.defects]
    reasons += [f"{i} appears twice" for i in dict.fromkeys(i for i in ids if ids.count(i) > 1)]
    return sc.pass_or_fail(reasons)


def notes_covered(out: Heuristics, needs: Witnesses) -> Pass | Fail:
    """Every note of the owner and every defect of the proof is answered by an entry that cites it."""
    said = {n.id for n in needs[NOTES].value.notes}
    covered = {c for e in out.entries for c in e.covers}
    reasons = [f"{n} is answered by no entry; write one that covers it" for n in sorted(said - covered)]
    reasons += [f"{c} is not a note of this run" for c in sorted(covered - said)]
    reasons += [f"entry {i}: say the rule, when it applies and its case" for i, e in enumerate(out.entries, 1)
                if not (e.rule.strip() and e.when.strip() and e.case.strip())]
    return sc.pass_or_fail(reasons)


# --- back to implement --------------------------------------------------------------

def measured_badly(out: Measured, _: Witnesses) -> list[str]:
    return [f"measure {f.id}: {f.what}" for f in out.measure] + [
        f"compare {f.id}: {f.what} (fix it, or keep it with the reason in kept)" for f in out.unexplained]


def defects_found(out: Review, _: Witnesses) -> list[str]:
    return [f"{d.id} ({d.capture}): {d.what}" for d in out.defects]


def _sentence(text: str) -> str:
    text = text.strip()
    return text[:1].upper() + text[1:].rstrip(".") + "." if text else ""


def _accept_question(_: Any, needs: Witnesses) -> str:
    r: Report = needs[REPORT].value
    problems = ([f"{len(r.measure)} measure{'s' * (len(r.measure) > 1)} failing"] if r.measure else []) + (
        [f"{len(r.unexplained)} worse than approved"] if r.unexplained else []) + (
        [f"{len(r.defects)} defect{'s' * (len(r.defects) > 1)} left"] if r.defects else []) + (
        ["over budget"] if r.over else [])
    return f"Keep this change? {_sentence(r.summary)}" + (f" Before you do: {', '.join(problems)}, under More."
                                                         if problems else "")


def _requirements_question(out: Requirements, _: Witnesses) -> str:
    first = len(out.slice.requirements)
    return ("Approve these changes? " + ("The one marked built now is made first; the rest wait." if first == 1 else
                                         f"The {first} marked built now are made first; the rest wait."))


def _region(*boxes: list[int]) -> tuple[int, ...]:
    """The smallest box holding every given one, so a before and an after are cut alike."""
    boxes = tuple(b for b in boxes if len(b) == 4)
    if not boxes:
        return ()
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    return (x0, y0, max(b[0] + b[2] for b in boxes) - x0, max(b[1] + b[3] for b in boxes) - y0)


def _show_moments(out: Moments, needs: Witnesses) -> list[Shown]:
    regions = needs[SNAPSHOT].value.regions
    screen = Shown("The screen as it is", "In your theme, on a computer and on a phone.",
                   tuple(Picture(SNAPSHOT.name, f"screen-{size}.png", label, _region(regions.get(f"screen-{size}.png", [])))
                         for size, label in (("desktop", "computer"), ("phone", "phone"))))
    return [screen, *(Shown(_sentence(m.statement), _sentence(m.case.text), id=m.id) for m in out.moments)]


def _show_requirements(out: Requirements, needs: Witnesses) -> list[Shown]:
    """Each change beside the part of the capture where its break happens: the problem, then what changes."""
    breaks = {b.id: b for m in needs[BREAKS].value.moments for b in m.breaks}
    shots = {c.id: c for c in needs[WALK].value.captures}

    def where(break_id: str) -> tuple[str, tuple[Picture, ...]]:
        b = breaks.get(break_id)
        last = shots.get(b.captures[-1]) if b else None
        return (_sentence(b.where) if b else ""), (
            (Picture(WALK.name, last.file, "on the screen today", _region(last.region)),) if last else ())

    items: list[Shown] = []
    for r in out.requirements:
        title, pictures = where(r.break_id)
        now = " Built now." if r.id in out.slice.requirements else ""
        items.append(Shown(title, f"{_sentence(r.need)}{now}", pictures))
    for d in out.deferred:
        title, pictures = where(d.break_id)
        items.append(Shown(title, f"Not now: {_sentence(d.reason)}", pictures))
    return items


def _show_accept(_: Any, needs: Witnesses) -> list[Shown]:
    """The screen before and after, cut alike, a line per change; measures, costs and the rest folded below."""
    snap, r, built = needs[SNAPSHOT].value, needs[REPORT].value, needs[IMPLEMENT].value
    owner = snap.theme
    shots = {x.id: x for x in needs[PROOF].value.captures}

    def side(size: str, theme: str) -> tuple[Picture, ...]:
        """Where the slice ends, before and after, cut alike around what changed."""
        before, after = shots.get(f"E-{theme}-{size}-before"), shots.get(f"E-{theme}-{size}-after")
        cut = _region(after.region) if after else ()
        return tuple(Picture(PROOF.name, x.file, label, cut) for x, label in ((before, "before"), (after, "after")) if x)

    items = [Shown("On a computer", "Where the change is, before and then after, in your theme.", side("desktop", owner)),
             Shown("On a phone", "Before and after, in your theme.", side("phone", owner))]
    items += [Shown(_sentence(c)) for c in built.changes]
    other = sc.other(owner)
    items.append(Shown(f"In the {other} theme", "Before and after, on a computer and on a phone.",
                       side("desktop", other) + side("phone", other), more=True))
    items.append(Shown("The screen as it opens", "After the change, in your theme.",
                       tuple(Picture(PROOF.name, shots[f"P-{owner}-{size}"].file, size, _region(shots[f"P-{owner}-{size}"].region))
                             for size in ("desktop", "phone") if f"P-{owner}-{size}" in shots), more=True))
    items.append(Shown("Cost", f"{r.tokens_new} new tokens of {r.budget_tokens}, {r.tokens_cached} read from the "
                               f"cache, {r.minutes} of {r.budget_minutes:g} minutes.", more=True))
    items += [Shown("Over budget", _sentence(o), more=True) for o in r.over]
    items += [Shown("A measure fails", _sentence(f.what), more=True) for f in r.measure]
    unexplained = {f.id for f in r.unexplained}
    items += [Shown("Worse than the approved version" if f.id in unexplained else "Kept on purpose",
                    _sentence(f.what), more=True) for f in r.compare]
    items += [Shown("Still wrong", _sentence(d.what),
                    (Picture(PROOF.name, shots[d.capture].file, "where it shows", _region(shots[d.capture].region)),)
                    if d.capture in shots else (), more=True) for d in r.defects]
    items += [Shown("Not verified", _sentence(u), more=True) for u in r.unverified]
    return items


def _moment_items(out: Moments) -> list[tuple[str, str]]:
    return [(m.id, m.statement) for m in out.moments]


def _keep_moments(out: Moments, ids: list[str]) -> Moments:
    return Moments(moments=[next(m for m in out.moments if m.id == i) for i in ids])


# --- the role ---------------------------------------------------------------------

INPUTS = {
    "method": Input("the design method the agents follow, a text file", kind="file"),
    "heuristics": Input("finishing rules read before designing, a text file", kind="file"),
    "theme": Input("the theme the owner judges in, dark or light", default="dark"),
    "data": Input("the data the product reads, frozen at the start: folder, or folder:where-in-the-project",
                  kind="folder", required=False),
    "blocked_gets": Input("GET paths with effects the walkthrough may not call, separated by commas",
                          required=False),
    "measure": Input('a command that measures the screen at {url} in {theme}, keeps files in {out}, and prints '
                     '{"failures": [{"id", "what"}]}', kind="command", required=False),
    "compare": Input('a command that compares {url} with the approved version at {approved_url} in {theme}, keeps '
                     'files in {out}, and prints {"failures": [{"id", "what"}]}', kind="command", required=False),
    "approved": Input("the commit of the version the owner last approved; the run's last commit when not given",
                      required=False),
    "budget": Input("new tokens a screen may cost: input outside the cache, cache writes and output",
                    default="150000"),
    "minutes": Input("minutes of agent turns a screen may take", default="30"),
}

CITE = ("Quote only what the snapshot shows: a citation names one of its files (screen.txt, controls.json or an "
        "api/ summary) and copies words from it exactly. Write no number in prose: a number goes in a count, whose "
        "query names a list of a response (copy a fact's query from inputs/snapshot.json) and which Jaynes computes; "
        "leave its value empty if you do not know it, and Jaynes tells you.")

ROLE = role("screen-design", inputs=INPUTS,
            about="Designs a screen from how the person uses it, following a design method kept in a file; it "
            "needs a preview and the screen's path in it.")
SNAPSHOT = ROLE.step("snapshot", output=Snapshot, run_by=take_snapshot, project="read", effect="read", max_turns=1)
MOMENTS = ROLE.step(
    "moments", output=Moments, needs=[SNAPSHOT], check=moments_cited, stop=stop(0.2, sampled=False),
    choose=Choice(items=_moment_items, keep=_keep_moments), inputs=["method"],
    question=lambda out, _: "Where do you get stuck on this screen? Choose two to five of these moments, most "
                            "important first.", shows=_show_moments,
    instructions="Offer the owner up to five moments where they get stuck or lost on this screen, each with a real "
                 "case from the data the screen shows, and the objects they think in during it. The owner chooses "
                 "two to five and orders them; offer concrete options rather than an open question. Read "
                 "inputs/snapshot/screen.txt, controls.json and the api/ summaries before the pictures. " + CITE)
OBJECTS = ROLE.step(
    "object-map", output=ObjectMap, needs=[SNAPSHOT, MOMENTS], check=objects_mapped, inputs=["method"],
    instructions="Map the things the owner thinks in on this screen: what each is to them, where it exists on the "
                 "screen and in storage (the API's responses), how many there are, what is missing for it to be "
                 "navigable, and its links to other things, each link a route the interface must offer. Every object "
                 "of the chosen moments in inputs/moments.json must be on the map, by name or alias. " + CITE)
PATHS = ROLE.step(
    "paths", output=Paths, needs=[SNAPSHOT, MOMENTS, OBJECTS], check=paths_cover, inputs=["method"],
    instructions="For each chosen moment, write the path the owner takes today, click by click, in the closed "
                 "vocabulary of the schema, starting with open at the screen's path (inputs/snapshot.json). Jaynes "
                 "performs each path on the product with the real data and captures every action; the path should go "
                 "as far as the owner would, including where it breaks.")
WALK = ROLE.step("walkthrough", output=Walk, needs=[PATHS], run_by=walk_paths, project="read", effect="read",
                 max_turns=1, max_files=160)
BREAKS = ROLE.step(
    "breaks", output=Breaks, needs=[MOMENTS, WALK], check=breaks_cite_captures, inputs=["method"],
    instructions="Look at the walkthrough: read each capture's text file in inputs/walkthrough/ first, then open the "
                 "pictures you need. For each chosen moment, say the exact point where the path breaks, citing the "
                 "captures of that moment that show it, or say no_break and why the path holds. Each capture's text "
                 "says how many controls were visible.")
REQUIREMENTS = ROLE.step(
    "requirements", output=Requirements, needs=[MOMENTS, OBJECTS, BREAKS, WALK], check=requirements_follow,
    stop=stop(0.2, sampled=False), inputs=["method", "heuristics"],
    question=_requirements_question, shows=_show_requirements,
    instructions="Write one requirement per break, in terms of objects and routes (a thing becomes a place with an "
                 "address; every name is a link), not components, in the order of the chosen moments, each with your "
                 "recommendation and why. Defer a break only with a reason. Mark the first slice: the smallest path "
                 "that works end to end, its moment, and where that moment must end, as a route and a text that shows "
                 "it ended there.")
RUNNER = ROLE.step("runner", output=sm.Suite, run_by=sm.find_suite, project="read", max_turns=1)
TEST = ROLE.step(
    "test", output=Test, needs=[PATHS, REQUIREMENTS], project="read", check=fails_first, inputs=["method"],
    instructions="Write the test of the first slice as a path in the closed vocabulary: the owner's clicks through "
                 "the slice's moment once it is built, starting with the same open as that moment's walkthrough path "
                 "(inputs/paths.json), with expectText and expectRoute where the owner should see something. Jaynes "
                 "adds the approved ending and performs the path on the product as it is: it must fail there. Copy a "
                 "line of what Jaynes reports as failure; when you do not know it yet, write your best guess and "
                 "Jaynes tells you what it reported.")
IMPLEMENT = ROLE.step(
    "implement", output=Built, needs=[RUNNER, REQUIREMENTS, TEST], project="write",
    may_change=("**", *(f"!{t}" for t in fb.TESTS)), check=slice_works, deadline=3600.0,
    inputs=["method", "heuristics"],
    instructions="Build the first slice so its path (inputs/test.json) reaches the approved ending, in the product's "
                 "approved brand and design system. Change the product's code, never its tests. List each change the "
                 "owner will see on the screen in changes, a line each. Read the heuristics "
                 "before designing. Run the suite yourself as inputs/runner.json says. Jaynes performs the slice's "
                 "path on your change with the real data and runs the suite; then it measures the screen and compares "
                 "it with the approved version, and sends you back what fails: fix it, or keep a comparison failure "
                 "on purpose with the reason in kept.")
MEASURE = ROLE.step("measure", output=Measured, needs=[IMPLEMENT], run_by=measure_slice, project="read",
                    effect="read", max_turns=1, max_files=100, inputs=["measure", "compare"],
                    back=Back(to=IMPLEMENT, when=measured_badly, rounds=2))
PROOF = ROLE.step("proof", output=Proof, needs=[REQUIREMENTS, TEST], run_by=prove, project="read", effect="read",
                  max_turns=1, max_files=100)
REVIEW = ROLE.step(
    "review", output=Review, needs=[REQUIREMENTS, IMPLEMENT, PROOF], check=defects_cite_proof,
    inputs=["method", "heuristics"], back=Back(to=IMPLEMENT, when=defects_found, rounds=2),
    instructions="Look at the proof in inputs/proof/: the changed screen in both themes, on desktop and phone, and "
                 "each action of the slice before, about 90 ms after, and settled. Look first in the owner's theme. "
                 "Find defects: volume that hides the rare case, a long name, missing data, a repeated item, a layout "
                 "that imitates another screen, an approved style that stopped applying, a rough transition. Cite the "
                 "capture of each. Defects go back to the implementer. Say what you could not verify.")
REPORT = ROLE.step("report", output=Report, needs=[IMPLEMENT, MEASURE, REVIEW], run_by=report, max_turns=1)
# the snapshot and the proof go with it: the screen before and after
ACCEPT = ROLE.step("accept", needs=[SNAPSHOT, REPORT, IMPLEMENT, PROOF], stop=stop(0.05, sampled=False),
                   question=_accept_question, shows=_show_accept)
NOTES = ROLE.step("notes", output=Notes, needs=[ACCEPT, REVIEW], run_by=collect_notes, max_turns=1)
ROLE.step(
    "heuristics", output=Heuristics, needs=[NOTES], check=notes_covered, inputs=["heuristics"],
    instructions="For every note in inputs/notes.json, the owner's corrections in this run and the defects only the "
                 "proof found, write or refine a heuristic: the rule, when it applies, and the case that supports it, "
                 "citing the notes it answers by id. A new heuristic is how the next agent gets it right without the "
                 "owner having to say it. Do not repeat a heuristic the file already has; refine it instead. With no notes, "
                 "write no entries.")
