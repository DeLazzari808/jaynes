"""screen-design on an invented product: Lantern, a book club's reading list, with its data frozen at the start.

Jaynes serves the product in its box and looks at it with its own Chrome, so these tests need Google Chrome. Each
guard of the role has a test that shows an agent's output refused, and one run goes from snapshot to heuristics.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from jaynes.conductor import AnswerRejected, Conductor, RunResult
from jaynes.roles import screen_design as sd
from jaynes.roles.references import chrome
from jaynes.runtime import TurnResult

pytestmark = pytest.mark.skipif(chrome() is None, reason="needs Google Chrome")

PRODUCT = Path(__file__).parent / "product"
BOOKS = "api/data-books-json.json"  # the summary of /data/books.json
Action = Callable[[Path], None]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def write(value: Any) -> Action:
    def act(ws: Path) -> None:
        (ws / "output.json").write_text(json.dumps(value))
    return act


def books(value: int | None = 3, **where: Any) -> dict[str, Any]:
    return {"query": {"endpoint": BOOKS, "items": "books", "where": [{"field": k, "equals": v} for k, v in where.items()]},
            "value": value}


MOMENTS = {"moments": [
    {"id": "M1", "statement": "I cannot tell whether a book is finished without opening it",
     "case": {"text": "Paper Lanterns was read already, and the list does not say so",
              "source": {"file": "screen.txt", "match": "Paper Lanterns"}},
     "objects": ["book", "status"], "counts": [books(1, status="done")]},
    {"id": "M2", "statement": "I do not know who suggested a book",
     "case": {"text": "The Salt Road came from someone, but the list hides who",
              "source": {"file": "screen.txt", "match": "The Salt Road"}},
     "objects": ["book", "member"]},
    {"id": "M3", "statement": "I search for the next book by its name",
     "case": {"text": "A Winter Orchard is the next one",
              "source": {"file": "screen.txt", "match": "A Winter Orchard"}},
     "objects": ["book"]},
]}
OBJECTS: dict[str, Any] = {"objects": [
    {"id": "O1", "name": "book", "aliases": ["books"], "meaning": "what the club reads together",
     "screen": {"source": {"file": "screen.txt", "match": "Reading list"}},
     "storage": {"source": {"file": BOOKS, "match": '"items": "books"'}},
     "count": books(3), "gap": "no state on the list", "links": [{"to": "O2", "route": "/members/<id>"}]},
    {"id": "O2", "name": "status", "aliases": [], "meaning": "whether the club read it",
     "screen": {"absent": True}, "storage": {"source": {"file": BOOKS, "match": '"status"'}},
     "count": {"query": {"endpoint": BOOKS, "items": "books", "count": "distinct", "field": "status"}, "value": 3},
     "gap": "never shown", "links": []},
]}
PATHS = {"paths": [
    {"moment": "M1", "steps": [{"action": "open", "to": "/index.html"},
                               {"action": "clickText", "text": "Paper Lanterns"},
                               {"action": "pressButton", "name": "Save note"}]},
    {"moment": "M3", "steps": [{"action": "open", "to": "/index.html"},
                               {"action": "type", "field": "Search books", "text": "orchard"}]},
]}
BREAKS = {"moments": [
    {"moment": "M1", "outcome": "break", "breaks": [
        {"id": "B1", "where": "the book opens without saying whether the club read it", "captures": ["M1-s02"]}]},
    {"moment": "M3", "outcome": "no_break", "reason": "the search finds the book by its name"},
]}
END = {"route": "/index.html#/books/<id>", "text": "Status: done"}
REQUIREMENTS = {"requirements": [
    {"id": "R1", "break_id": "B1", "objects": ["O1", "O2"], "route": "/index.html#/books/<id>",
     "need": "a book's place says whether the club read it", "recommendation": "show the status beside who picked it",
     "why": "it is the question the owner opens the book to answer"}],
    "deferred": [], "slice": {"requirements": ["R1"], "moment": "M1", "end": END}}
TEST = {"moment": "M1", "path": [{"action": "open", "to": "/index.html"}, {"action": "clickText", "text": "Paper Lanterns"}],
        "failure": "the text 'Status: done' does not appear"}
STATUS = ('document.getElementById("who").textContent = "Picked by " + b.picked_by;',
          'document.getElementById("who").textContent = "Picked by " + b.picked_by + " \\u00b7 Status: " + b.status;')


# the agent maps what every moment it offered needs: a stop does not hold the run, so the steps after the moments run
# on all of them before the owner chooses, and again on the chosen
MEMBER = {"id": "O3", "name": "member", "aliases": ["members"], "meaning": "who suggested a book",
          "screen": {"absent": True}, "storage": {"source": {"file": BOOKS, "match": '"picked_by"'}},
          "count": {"query": {"endpoint": BOOKS, "items": "books", "count": "distinct", "field": "picked_by"},
                    "value": 2}, "gap": "never on the list", "links": []}
M2_PATH = {"moment": "M2", "steps": [{"action": "open", "to": "/index.html"},
                                     {"action": "clickText", "text": "The Salt Road"}]}
M2_HOLDS = {"moment": "M2", "outcome": "no_break", "reason": "the book's page says who picked it"}


def offered(ws: Path) -> list[str]:
    return [m["id"] for m in json.loads((ws / "inputs" / "moments.json").read_text())["moments"]]


def object_map(ws: Path) -> None:
    write({"objects": OBJECTS["objects"] + ([MEMBER] if "M2" in offered(ws) else [])})(ws)


def paths(ws: Path) -> None:
    by = {x["moment"]: x for x in [*PATHS["paths"], M2_PATH]}
    write({"paths": [by[m] for m in offered(ws)]})(ws)


def breaks(ws: Path) -> None:
    by = {x["moment"]: x for x in [*BREAKS["moments"], M2_HOLDS]}
    write({"moments": [by[m] for m in offered(ws)]})(ws)


def build(ws: Path) -> None:
    app = ws / "project" / "app.js"
    app.write_text(app.read_text().replace(*STATUS))
    write({"summary": "A book's page says whether the club read it, beside who picked it", "files": ["app.js"],
           "changes": ["a book's page shows its status beside who picked it"]})(ws)


def heuristics(ws: Path) -> None:
    notes = json.loads((ws / "inputs" / "notes.json").read_text())["notes"]
    write({"entries": [{"rule": "Say a thing's state where its name is", "when": "lists of things with a state",
                        "case": "this run", "covers": [n["id"] for n in notes]}] if notes else []})(ws)


DEFAULT: dict[str, Action] = {
    "moments": write(MOMENTS), "object-map": object_map, "paths": paths, "breaks": breaks,
    "requirements": write(REQUIREMENTS), "test": write(TEST), "implement": build,
    "review": write({"defects": [], "unverified": ["the phone layout of a long title"]}), "heuristics": heuristics}


class Agent:
    """The scripted designer: each step writes its planned output, unless a test plans another for that turn."""

    name = "claude"

    def __init__(self, plan: dict[tuple[str, int], Action] | None = None) -> None:
        self.plan = plan or {}
        self.counts: dict[str, int] = {}
        self.prompts: dict[str, list[str]] = {}

    def turn(self, workspace: Path, scratch: Path, prompt: str, deadline: float, web: bool = False,
             max_messages: int | None = None) -> TurnResult:
        step = json.loads((workspace / "step.json").read_text())["step"]
        self.counts[step] = self.counts.get(step, 0) + 1
        self.prompts.setdefault(step, []).append(prompt)
        self.plan.get((step, self.counts[step]), DEFAULT[step])(workspace)
        return TurnResult(10200, 300, "designer-1", tokens_cached=9000)  # 1200 new input, 9000 read back


TOOL = """import json, pathlib, sys
config, url, theme, out = sys.argv[1], sys.argv[2], sys.argv[3], pathlib.Path(sys.argv[4])
assert url.startswith("http://127.0.0.1:") and theme == "dark", (url, theme)
(out / "report.txt").write_text("measured " + url)
print(json.dumps({"failures": json.loads(pathlib.Path(config).read_text())}))
"""


class Lantern:
    """The product as a git repository, its live data, the person's method files and measuring tools."""

    def __init__(self, tmp: Path) -> None:
        self.tmp, self.repo = tmp, tmp / "lantern"
        shutil.copytree(PRODUCT / "app", self.repo)
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "list.test.js").write_text("// the list shows every book\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start")
        self.data = tmp / "live-data"
        shutil.copytree(PRODUCT / "data", self.data)
        (tmp / "method.md").write_text("Design from use: moments, objects, walkthrough, requirements, slices.")
        (tmp / "heuristics.md").write_text("A thing's state sits beside its name.")
        (tmp / "tool.py").write_text(TOOL)
        self.measured, self.compared = tmp / "measure.json", tmp / "compare.json"
        self.measured.write_text("[]")
        self.compared.write_text("[]")

    def inputs(self, **more: str) -> dict[str, str]:
        tool = f"{sys.executable} {self.tmp / 'tool.py'}"
        return {"method": str(self.tmp / "method.md"), "heuristics": str(self.tmp / "heuristics.md"),
                "data": f"{self.data}:data", "blocked_gets": "api/covers",
                "measure": f"{tool} {self.measured} {{url}} {{theme}} {{out}}",
                "compare": f"{tool} {self.compared} {{approved_url}} {{theme}} {{out}}", **more}

    def start(self, agent: Agent, **more: str) -> tuple[Conductor, str, RunResult]:
        c = Conductor(self.tmp / "home", {"claude": lambda: agent})
        started: dict[str, Any] = {"preview": {"command": f"{sys.executable} -m http.server {{port}} --bind 127.0.0.1",
                               "path": "/index.html", "ports": []}, "inputs": self.inputs(**more),
                                   "ref": "jaynes.roles.screen_design:ROLE", "runtimes": {}}
        out = c.run(sd.ROLE, "Design the reading list from how the club uses it", started=started, project=self.repo)
        return c, c.store.runs()[0], out


@pytest.fixture
def lantern(tmp_path: Path) -> Lantern:
    return Lantern(tmp_path)


def go(c: Conductor, run: str, step: str, answer: Any = "accept", **kw: Any) -> RunResult:
    c.answer(sd.ROLE, run, step, answer, by="person", **kw)
    return c.run(sd.ROLE, "", run)


def rejected(c: Conductor, run: str, step: str) -> list[str]:
    return [r for e in c.store.events("TurnRejected", run_id=run) if e.payload["step"] == step
            for r in e.payload["reasons"]]


def to_requirements(c: Conductor, run: str) -> RunResult:
    return go(c, run, "moments", "choose", chosen=["M1", "M3"])  # the object map does not stop


def test_the_snapshot_sees_the_screen_with_the_frozen_data_and_summarizes_its_api(lantern: Lantern) -> None:
    c, run, out = lantern.start(Agent())
    assert (out.status, out.step) == ("waiting", "moments")
    (lantern.data / "books.json").write_text('{"books": []}')  # the person keeps working: the run does not see it
    snap = c.output(run, sd.SNAPSHOT)
    assert snap is not None and snap.theme == "dark" and snap.path == "/index.html"
    assert {"screen.txt", "controls.json", "screen-desktop.png", "screen-phone.png", BOOKS} <= set(snap.files)
    assert {"method": "GET", "path": "/api/covers/refresh"} in snap.blocked
    assert [(f.query.items, f.value) for f in snap.facts][:1] == [("books", 3)]
    ws = next((lantern.tmp / "home" / "runs").glob("*/moments/turn-1/workspace"))
    assert (ws / "inputs" / "snapshot" / BOOKS).is_file() and not (ws / "inputs" / "snapshot" / "private").exists()


def test_a_fabricated_case_is_refused(lantern: Lantern) -> None:
    made_up = json.loads(json.dumps(MOMENTS))
    made_up["moments"][1]["case"]["source"]["match"] = "The Glass Garden"
    c, run, out = lantern.start(Agent({("moments", 1): write(made_up)}))
    assert out.step == "moments"
    assert "M2 case: 'The Glass Garden' does not appear in screen.txt; copy the words exactly" in rejected(c, run, "moments")


def test_numbers_go_in_counts_jaynes_computes(lantern: Lantern) -> None:
    loose = json.loads(json.dumps(MOMENTS))
    loose["moments"][0]["statement"] = "I open 3 books to find the finished one"
    blank = json.loads(json.dumps(MOMENTS))
    blank["moments"][0]["counts"] = [books(None, status="done")]
    agent = Agent({("moments", 1): write(loose), ("moments", 2): write(blank)})
    c, run, _ = lantern.start(agent)
    reasons = rejected(c, run, "moments")
    assert any("M1 statement: 3 written in prose" in r for r in reasons)
    assert "M1 count 1: Jaynes computed 1 for this query; write it as the value" in reasons
    assert agent.counts["moments"] == 3


def test_a_floor_stop_refuses_an_agents_answer(lantern: Lantern) -> None:
    c, run, _ = lantern.start(Agent())
    with pytest.raises(AnswerRejected, match="only a person answers it"):
        c.answer(sd.ROLE, run, "moments", "choose", chosen=["M1", "M2"], by="agent")
    with pytest.raises(AnswerRejected, match="keep 2 to 5 moments, not 1"):
        c.answer(sd.ROLE, run, "moments", "choose", chosen=["M1"], by="person")


def test_an_object_of_a_moment_missing_from_the_map_and_a_wrong_count_are_refused(lantern: Lantern) -> None:
    wrong = json.loads(json.dumps(OBJECTS))
    wrong["objects"] = wrong["objects"][:1]
    wrong["objects"][0]["count"]["value"] = 7
    wrong["objects"][0]["links"] = []
    c, run, _ = lantern.start(Agent({("object-map", 1): write(wrong)}))
    out = go(c, run, "moments", "choose", chosen=["M1", "M3"])
    assert (out.status, out.step) == ("waiting", "requirements")  # the second attempt passed, and nobody was asked
    reasons = rejected(c, run, "object-map")
    assert "status, an object of M1, is not on the map; add it, or name it as an alias" in reasons
    assert "O1 count: the value is 7, but the query computes 3" in reasons


def test_a_path_outside_the_vocabulary_is_refused(lantern: Lantern) -> None:
    scripted = json.loads(json.dumps(PATHS))
    scripted["paths"][0]["steps"].append({"action": "evaluate", "script": "fetch('/api/notes', {method: 'POST'})"})
    c, run, _ = lantern.start(Agent({("paths", 1): write(scripted)}))
    to_requirements(c, run)
    assert any("paths.0.steps.3" in r and "evaluate" in r for r in rejected(c, run, "paths"))


def test_the_walkthrough_refuses_posts_and_the_listed_gets_and_breaks_cite_captures_of_their_moment(
        lantern: Lantern) -> None:
    wrong = json.loads(json.dumps(BREAKS))
    wrong["moments"][0]["breaks"][0]["captures"] = ["M1-s09", "M3-s01"]
    c, run, out = lantern.start(Agent({("breaks", 1): write(wrong)}))
    out = to_requirements(c, run)
    assert (out.status, out.step) == ("waiting", "requirements")
    walk = c.output(run, sd.WALK)
    assert walk is not None and [x.id for x in walk.captures] == ["M1-s01", "M1-s02", "M1-s03", "M3-s01", "M3-s02"]
    assert {"method": "POST", "path": "/api/notes"} in walk.blocked
    assert {"method": "GET", "path": "/api/covers/refresh"} in walk.blocked
    reasons = rejected(c, run, "breaks")
    assert "B1 cites M1-s09, which is not a capture Jaynes took" in reasons
    assert "B1 cites M3-s01, a capture of M3, not of M1" in reasons


def test_a_requirement_citing_no_break_or_out_of_order_is_refused(lantern: Lantern) -> None:
    breaks = {"moments": [
        {"moment": "M1", "outcome": "break", "breaks": [{"id": "B1", "where": "no status", "captures": ["M1-s02"]}]},
        M2_HOLDS,  # the run maps every moment offered before the owner chooses
        {"moment": "M3", "outcome": "break", "breaks": [{"id": "B2", "where": "no state", "captures": ["M3-s02"]}]}]}
    reqs = json.loads(json.dumps(REQUIREMENTS))
    second = dict(reqs["requirements"][0], id="R2", break_id="B2")
    reqs["requirements"] = [second, dict(reqs["requirements"][0], id="R3", break_id="B7"), reqs["requirements"][0]]
    c, run, _ = lantern.start(Agent({("breaks", 1): write(breaks), ("requirements", 1): write(reqs)}))
    to_requirements(c, run)
    reasons = rejected(c, run, "requirements")
    assert "R3 cites B7, which is not a break" in reasons
    assert "the requirements are not in the order of the chosen moments" in reasons


def test_the_slice_test_must_fail_before_the_change(lantern: Lantern) -> None:
    passing = json.loads(json.dumps(REQUIREMENTS))
    passing["slice"]["end"]["text"] = "Picked by Tomas"  # already there
    plan = {("requirements", 1): write(passing)}
    plan.update({("test", n): write(dict(TEST, failure="anything")) for n in (1, 2, 3)})
    c, run, out = lantern.start(Agent(plan))  # no stop holds it: it blocks at the test before anyone answers
    assert (out.status, out.step) == ("blocked", "test")
    assert "the path already reaches the ending on the product as it is; it must fail before the change" in out.reasons


def test_implement_may_not_change_the_tests(lantern: Lantern) -> None:
    def cheat(ws: Path) -> None:
        build(ws)
        (ws / "project" / "tests" / "list.test.js").write_text("// nothing to see\n")

    c, run, _ = lantern.start(Agent({("implement", 1): cheat}))
    to_requirements(c, run)
    go(c, run, "requirements")
    assert any("may not change" in r or "may change" in r for r in rejected(c, run, "implement"))
    assert any("tests/list.test.js" in r for r in rejected(c, run, "implement"))


def test_a_measured_violation_goes_back_to_implement_and_stays_in_sight(lantern: Lantern) -> None:
    lantern.measured.write_text(json.dumps([{"id": "spacing", "what": "13px is off the spacing scale"}]))
    agent = Agent()
    c, run, _ = lantern.start(agent)
    to_requirements(c, run)
    out = go(c, run, "requirements")
    assert (out.status, out.step) == ("waiting", "accept")
    back = [e.payload for e in c.store.events("StepReturned", run_id=run)]
    # the second build changed nothing, so there was nothing new to measure: the run went on with the failure
    assert [(b["step"], b["to"]) for b in back] == [("measure", "implement")]
    assert "measure spacing: 13px is off the spacing scale" in agent.prompts["implement"][1]
    [opened] = [e.payload for e in c.store.events("StopOpened", run_id=run) if e.payload["step"] == "accept"]
    assert "Before you do: 1 measure failing, under More." in opened["question"]
    assert {"title": "A measure fails", "text": "13px is off the spacing scale.", "pictures": [], "id": "",
            "more": True} in opened["shown"]


def test_a_note_without_a_heuristic_is_refused(lantern: Lantern) -> None:
    lazy = write({"entries": [{"rule": "r", "when": "w", "case": "c", "covers": ["N1"]}]})
    c, run, _ = lantern.start(Agent({("heuristics", n): lazy for n in (1, 2, 3)}))
    go(c, run, "moments", "return", note="the moments should come from the inbox, not the list")
    go(c, run, "moments", "choose", chosen=["M1", "M3"], note="M1 matters most")
    go(c, run, "requirements")
    out = go(c, run, "accept")
    assert (out.status, out.step) == ("blocked", "heuristics")
    assert "N2 is answered by no entry; write one that covers it" in out.reasons


def test_a_whole_run_from_snapshot_to_heuristics(lantern: Lantern) -> None:
    lantern.compared.write_text(json.dumps([{"id": "chars", "what": "the detail says more than the approved one"}]))

    def keep(ws: Path) -> None:
        build(ws)
        out = json.loads((ws / "output.json").read_text())
        (ws / "output.json").write_text(json.dumps({**out, "kept": [{"id": "chars", "why": "the status is the point"}]}))

    defect = write({"defects": [{"id": "D1", "what": "the status wraps under the name", "capture": "T-s02-settled"}],
                    "unverified": []})
    agent = Agent({("implement", 1): build, **{("implement", n): keep for n in (2, 3, 4)}, ("review", 1): defect})
    c, run, out = lantern.start(agent)
    assert out.step == "moments"
    assert "Design from use" in agent.prompts["moments"][0] and "beside its name" not in agent.prompts["moments"][0]
    assert go(c, run, "moments", "choose", chosen=["M1", "M3"]).step == "requirements"
    out = go(c, run, "requirements")
    assert (out.status, out.step) == ("waiting", "accept")
    assert "beside its name" in agent.prompts["implement"][0]  # the heuristics, where it designs
    returned = [(e.payload["step"], e.payload["reasons"]) for e in c.store.events("StepReturned", run_id=run)]
    assert returned[0][0] == "measure" and "compare chars" in returned[0][1][0]
    assert ("review", ["D1 (T-s02-settled): the status wraps under the name"]) in returned
    proof = c.output(run, sd.PROOF)
    assert proof is not None and proof.errors == []
    assert {"P-dark-desktop", "P-light-phone", "T-s02-before", "T-s02-90ms", "T-s02-settled"} <= {x.id for x in proof.captures}
    report = c.output(run, sd.REPORT)
    assert report is not None and report.unexplained == [] and report.tokens_cached > 0
    assert report.tokens_new == sum(x.tokens_new for x in report.cost) and report.budget_tokens == 150000
    moments = next(x for x in report.cost if x.step == "moments")
    assert (moments.turns, moments.tokens_new, moments.tokens_cached) == (1, 1500, 9000)
    assert {x.step for x in report.cost} >= {"moments", "implement", "review"}
    out = go(c, run, "accept", note="the status could be a quiet tag")
    assert out.status == "finished", out
    notes = c.output(run, sd.NOTES)
    assert notes is not None and [(n.id, n.kind) for n in notes.notes] == [("N1", "accept"), ("P1", "defect")]
    [branch] = [e.payload for e in c.store.events("BranchCreated", run_id=run)]
    assert "Status: " in git(lantern.repo, "show", f"{branch['branch']}:app.js")
    assert git(lantern.repo, "status", "--short") == ""  # the person's working tree is untouched


def test_jaynes_output_prints_a_steps_checked_output(lantern: Lantern, capsys: pytest.CaptureFixture[str]) -> None:
    from jaynes import cli
    _, run, _ = lantern.start(Agent())
    capsys.readouterr()
    cli.main(["output", run, "moments", "--home", str(lantern.tmp / "home")])
    assert json.loads(capsys.readouterr().out)["moments"][0]["id"] == "M1"
    with pytest.raises(SystemExit, match="no checked output"):
        cli.main(["output", run, "heuristics", "--home", str(lantern.tmp / "home")])


def test_the_object_map_never_stops(lantern: Lantern) -> None:
    """The owner judges the map in the requirements it leads to; asked about it as data, they could not tell."""
    assert sd.OBJECTS.stop is None
    c, run, _ = lantern.start(Agent())
    to_requirements(c, run)
    stops = {e.payload["step"] for e in c.store.events("StopOpened", run_id=run)}
    assert stops == {"moments", "requirements", "accept"}


def test_each_stop_shows_sentences_and_pictures_the_owner_judges(lantern: Lantern) -> None:
    c, run, _ = lantern.start(Agent())
    def shown(step: str) -> list[dict[str, Any]]:
        return [e.payload["shown"] for e in c.store.events("StopOpened", run_id=run) if e.payload["step"] == step][-1]

    moments = shown("moments")
    assert [p["file"] for p in moments[0]["pictures"]] == ["screen-desktop.png", "screen-phone.png"]
    x, y, w, h = moments[0]["pictures"][0]["region"]  # where the list is, not the empty rest of the screen
    assert (x, y) == (0, 0) and 480 <= w < 1440 and 270 <= h < 900
    assert [(m["id"], m["title"]) for m in moments[1:]] == [
        ("M1", "I cannot tell whether a book is finished without opening it."),
        ("M2", "I do not know who suggested a book."), ("M3", "I search for the next book by its name.")]
    to_requirements(c, run)
    [req] = shown("requirements")  # one break, beside the part of the capture where it happens
    assert req["title"] == "The book opens without saying whether the club read it."
    assert req["text"] == "A book's place says whether the club read it. Built now."
    [pic] = req["pictures"]
    assert (pic["file"], pic["caption"]) == ("M1-s02.png", "on the screen today") and pic["region"][2] < 1440
    opened = [e.payload for e in c.store.events("StopOpened", run_id=run) if e.payload["step"] == "requirements"][-1]
    assert opened["question"] == "Approve these changes? The one marked built now is made first; the rest wait."
    go(c, run, "requirements")
    accept = shown("accept")
    front = [x for x in accept if not x["more"]]
    assert [(x["title"], [(p["file"], p["caption"]) for p in x["pictures"]]) for x in front] == [
        ("On a computer", [("E-dark-desktop-before.png", "before"), ("E-dark-desktop-after.png", "after")]),
        ("On a phone", [("E-dark-phone-before.png", "before"), ("E-dark-phone-after.png", "after")]),
        ("A book's page shows its status beside who picked it.", [])]
    assert front[0]["pictures"][0]["region"] == front[0]["pictures"][1]["region"]  # cut alike
    folded = [x["title"] for x in accept if x["more"]]
    assert folded[:3] == ["In the light theme", "The screen as it opens", "Cost"] and "Not verified" in folded


def test_no_question_names_a_slice_a_route_or_a_capture(lantern: Lantern) -> None:
    c, run, _ = lantern.start(Agent())
    to_requirements(c, run)
    go(c, run, "requirements")
    questions = [e.payload["question"] for e in c.store.events("StopOpened", run_id=run)]
    accept = [e.payload["question"] for e in c.store.events("StopOpened", run_id=run) if e.payload["step"] == "accept"]
    assert accept[-1].startswith("Keep this change? ")
    for q in questions:
        assert "slice" not in q.lower() and "/" not in q and "M1" not in q and "R1" not in q, q


def test_accept_shows_where_the_slice_ends_before_and_after_cut_around_what_changed(lantern: Lantern) -> None:
    """Accepting is comparing two pictures: of the book's page, reached by the slice's path, not of the list."""
    c, run, _ = lantern.start(Agent())
    to_requirements(c, run)
    go(c, run, "requirements")
    proof = c.output(run, sd.PROOF)
    assert proof is not None
    ends = {x.id: x for x in proof.captures if x.kind.startswith("end-")}
    assert set(ends) == {f"E-{t}-{s}-{k}" for t in ("dark", "light") for s in ("desktop", "phone") for k in ("before", "after")}
    [checked] = [e.payload for e in c.store.events("Checked", run_id=run) if e.payload["step"] == "proof"]

    def picture(name: str) -> bytes:
        return (lantern.tmp / "home" / "objects" / checked["files"][name]).read_bytes()

    for size in ("desktop", "phone"):  # the end differs before and after; the list as it opens does not
        assert picture(f"E-dark-{size}-before.png") != picture(f"E-dark-{size}-after.png")
        _, _, w, h = ends[f"E-dark-{size}-after"].region
        assert w < (1440 if size == "desktop" else 390) or h < (900 if size == "desktop" else 844)
    [opened] = [e.payload for e in c.store.events("StopOpened", run_id=run) if e.payload["step"] == "accept"]
    shown = [p["file"] for x in opened["shown"] if not x["more"] for p in x["pictures"]]
    assert shown == ["E-dark-desktop-before.png", "E-dark-desktop-after.png", "E-dark-phone-before.png",
                     "E-dark-phone-after.png"]
