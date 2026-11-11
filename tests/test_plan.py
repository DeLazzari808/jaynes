"""A request becomes a plan of runs: each starts once the runs it follows delivered, from where their delivery
landed, and hears what they delivered."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest

from jaynes import cli, plan
from jaynes.conductor import Conductor, project
from tests.test_merge import git
from tests.test_review import get, serving

RUNTIME = ["planner=tests.planned:Agent", "quick=tests.merging:Agent"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "start")
    return r


@pytest.fixture
def pending(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The runs a plan started, as the ``jaynes run`` each would run in the background, in the order started."""
    started: list[list[str]] = []

    def later(c: Conductor, req: plan.Request, p: plan.Planned, task: str, base: str | None) -> None:
        started.append(["run", p.run, "--home", str(c.home), "--role", p.role, "--task", task,
                        "--request", f"{req.id}:{p.name}", *(["--project", req.project] if req.project else []),
                        *(["--base", base] if base else []),
                        *[a for r in sorted(p.runtimes.items()) for a in ("--runtime", "=".join(r))]])

    monkeypatch.setattr(plan, "start_run", later)
    return started


def runs(*items: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**x, "runtimes": dict(r.split("=", 1) for r in RUNTIME)} for x in items]


def start(tmp_path: Path, capsys: pytest.CaptureFixture[str], data: dict[str, Any],
          repo: Path | None = None) -> dict[str, Any]:
    (tmp_path / "plan.json").write_text(json.dumps(data))
    cli.main(["plan", str(tmp_path / "plan.json"), "--home", str(tmp_path / "home"),
              *(["--project", str(repo)] if repo else [])])
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def drain(pending: list[list[str]], capsys: pytest.CaptureFixture[str]) -> list[str]:
    """Run what was started until nothing more starts; the run ids, in order."""
    done: list[str] = []
    while pending:
        args = pending.pop(0)
        cli.main(args)
        capsys.readouterr()
        done.append(args[1])
    return done


TWO = {"request": "A dark look, then the home screen in it",
       "runs": runs({"name": "look", "role": "tests.planned:LOOK", "task": "Paint a dark look"},
                    {"name": "screen", "role": "tests.planned:SCREEN", "task": "Build the home screen",
                     "after": ["look"]})}


def test_the_next_run_starts_once_the_one_before_delivered_working_from_its_branch(
        tmp_path: Path, repo: Path, pending: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    out = start(tmp_path, capsys, TWO, repo)

    assert out["status"] == "working" and out["page"].endswith(f"/request/{out['request']}")
    assert [(r["name"], r["state"]) for r in out["runs"]] == [("look", "working"), ("screen", "planned")]
    assert out["runs"][1]["why"] == "after look"
    assert len(pending) == 1  # only the run that follows none

    look, screen = drain(pending, capsys)

    c = Conductor(tmp_path / "home")
    first, second = project(c.store.events(run_id=look)), project(c.store.events(run_id=screen))
    assert first.branch is not None and second.started is not None
    assert second.started["project"]["base"] == first.branch["commit"]  # it works from the look's branch
    assert second.started["request"] == {"id": out["request"], "name": "screen"}
    built = second.step("build").checked
    assert built is not None
    said = json.loads((c.home / "objects" / f"{built['output_hash']}.json").read_text())
    assert said["text"].startswith("body { color: #102030; }")  # the look was in its copy
    assert f"look (look, run {look}): Paint a dark look. On the branch jaynes/{look}" in said["text"]
    assert "Files it changed: look.css." in said["text"]
    [req] = plan.requests(c, out["request"])
    assert plan.status(c, req) == "delivered"


def test_after_a_merge_the_next_run_works_from_the_branch_it_merged_into(
        tmp_path: Path, repo: Path, pending: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    out = start(tmp_path, capsys, {"request": "Fix add, then build a screen", "runs": runs(
        {"name": "fix", "role": "tests.merging:ROLE", "task": "Make add add"},
        {"name": "screen", "role": "tests.planned:SCREEN", "task": "Build the screen", "after": ["fix"]})}, repo)

    fix, screen = drain(pending, capsys)

    c = Conductor(tmp_path / "home")
    first, second = project(c.store.events(run_id=fix)), project(c.store.events(run_id=screen))
    assert first.merged and not first.finished  # merged alone; its stop is still open, and the plan went on
    assert second.started is not None and second.started["project"]["base"] == git(repo, "rev-parse", "HEAD")
    assert "a + b" in git(repo, "show", f"{second.started['project']['base']}:calc.py")
    assert out["request"] in {e.run_id for e in c.store.events("RequestPlanned")}


def test_a_run_with_no_project_hands_its_outputs_to_the_next(
        tmp_path: Path, pending: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    start(tmp_path, capsys, {"request": "Notes, then more notes", "runs": runs(
        {"name": "first", "role": "tests.planned:NOTES", "task": "Write notes"},
        {"name": "second", "role": "tests.planned:NOTES", "task": "Write more", "after": ["first"]})})

    first, second = drain(pending, capsys)

    c = Conductor(tmp_path / "home")
    task = str(project(c.store.events(run_id=second)).started["task"])  # type: ignore[index]
    assert task.startswith("Write more\n\nThis run is part of a request, in the person's words: \"Notes, then more")
    assert f'first (notes, run {first}): Write notes. Its outputs: {{"write": {{"text": "notes' in task


def test_a_run_that_will_not_deliver_stops_the_runs_after_it(
        tmp_path: Path, repo: Path, pending: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    out = start(tmp_path, capsys, TWO, repo)
    look = pending.pop(0)[1]
    c = Conductor(tmp_path / "home")
    c.store.append("RunStarted", {"role": "look", "task": "Paint", "fingerprint": "x",
                                  "request": {"id": out["request"], "name": "look"}}, run_id=look)
    c.dismiss(look)

    assert plan.advance(c, out["request"]) == []
    [req] = plan.requests(c, out["request"])
    assert [(x.planned.name, x.state, x.why) for x in plan.places(c, req)] == [
        ("look", "stopped", "it was dropped"), ("screen", "stopped", "look will not deliver")]
    assert plan.status(c, req) == "stopped"


@pytest.mark.parametrize(("runs_", "reason"), [
    ([{"name": "a", "role": "nope", "task": "t"}], "run a: no role 'nope'"),
    ([{"name": "a", "role": "small-change", "task": ""}], "run a: task must say what this run does"),
    ([{"name": "a", "role": "small-change", "task": "t", "after": ["b"]},
      {"name": "b", "role": "small-change", "task": "t"}], "run a follows b, which must be listed before it"),
    ([{"name": "a", "role": "small-change", "task": "t"}, {"name": "a", "role": "small-change", "task": "t"}],
     "run a is listed twice"),
    ([{"name": "A b", "role": "small-change", "task": "t"}], "run 1: name must be lowercase"),
    ([{"name": "a", "role": "tests.planned:LOOK", "task": "t"}, {"name": "b", "role": "tests.planned:LOOK", "task": "t"},
      {"name": "c", "role": "small-change", "task": "t", "after": ["a", "b"]}],
     "run c follows more than one run that does not merge"),
])
def test_a_plan_is_refused_with_why(runs_: list[dict[str, Any]], reason: str, tmp_path: Path) -> None:
    with pytest.raises(plan.PlanError) as err:
        plan.parse({"request": "r", "runs": runs_}, has_project=True, home=tmp_path)
    assert any(r.startswith(reason) for r in err.value.reasons), err.value.reasons


def test_a_refused_plan_records_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "plan.json").write_text(json.dumps({"request": "", "runs": []}))
    with pytest.raises(SystemExit, match="the plan is refused"):
        cli.main(["plan", str(tmp_path / "plan.json"), "--home", str(tmp_path / "home")])
    assert not list(Conductor(tmp_path / "home").store.events("RequestPlanned"))


def test_the_page_looks_again_and_starts_what_a_crash_left(
        tmp_path: Path, repo: Path, pending: list[list[str]], capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    start(tmp_path, capsys, TWO, repo)
    with monkeypatch.context() as m:
        def dies(c: Conductor, run_id: str) -> None:  # the look delivers, and its process dies
            pass

        m.setattr(cli, "_next_in_plan", dies)
        cli.main(pending.pop(0))
    capsys.readouterr()
    c = Conductor(tmp_path / "home")
    screen = plan.requests(c)[0].runs[1].run

    plan.advance_all(c)
    plan.advance_all(c)
    assert [a[1] for a in pending] == [screen]  # started once

    pending.clear()  # and the process it started died before the run recorded its start
    c.clock = lambda: time.time() + plan.GRACE
    plan.advance_all(c)
    assert [a[1] for a in pending] == [screen]


def test_the_page_shows_the_plan_its_next_run_and_each_runs_place(
        tmp_path: Path, repo: Path, pending: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    out = start(tmp_path, capsys, TWO, repo)
    look = pending[0][1]
    base, server = serving(tmp_path / "home")
    try:
        home = get(base + "/")
        running = home[home.index("Running now"):]
        assert "A dark look, then the home screen in it" in running and "Next: screen, after look" in running
        assert "0 of 2 runs delivered" in running and f'href="/request/{out["request"]}"' in running

        cli.main(pending.pop(0))
        capsys.readouterr()
        req = get(f"{base}/request/{out['request']}")
        assert "look: Paint a dark look" in req and "screen: Build the home screen" in req
        assert f'href="/run/{look}"' in req and "after look" in req and "delivered" in req

        run = get(f"{base}/run/{look}")
        assert f'Run look, 1 of 2 in the plan of <a href="/request/{out["request"]}">' in run
        assert "No request q1" in get(base + "/request/q1")
    finally:
        server.shutdown()


def test_a_stop_says_what_each_step_does_and_which_runs_of_the_plan_wait_for_it(
        tmp_path: Path, repo: Path, pending: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    data = {"request": "A look, then two screens in it",
            "runs": runs({"name": "look", "role": "tests.planned:TOLD", "task": "Paint a dark look"},
                         {"name": "home", "role": "tests.planned:SCREEN", "task": "Build home", "after": ["look"]},
                         {"name": "settings", "role": "tests.planned:SCREEN", "task": "Build settings",
                          "after": ["home"]})}
    out = start(tmp_path, capsys, data, repo)
    look = pending[0][1]
    cli.main(pending.pop(0))
    capsys.readouterr()
    base, server = serving(tmp_path / "home")
    try:
        stop = get(f"{base}/stop/{look}/pick")
        assert '<p class="ask">You choose whether the look stays. Only its colours are kept; the screen is a sample.' in stop
        way = stop[stop.index('<details class="way">'):]
        line = way[:way.index("</summary>")]  # the steps in one line, each saying what it does when pointed at
        assert '<li class="done" title="An agent paints the look."><i></i>paint</li>' in line
        assert '<li class="here" title="You choose whether' in line and "then 2 screen runs" in line
        steps = way[way.index("</summary>"):]
        assert re.search(r'<b>paint<span class="who">agent</span></b>.*An agent paints the look\.', steps, re.S)
        assert '<b>pick<span class="who">you</span></b>' in steps  # a stop that waits always asks
        after = steps[steps.index("Then"):]
        assert f'href="/request/{out["request"]}"' in after
        assert "<b>home, settings</b>" in after and "Builds a screen in the" in after
        assert "Paints a look and asks" not in stop  # what the role is for is on its own page

        assert "An agent paints the look." in get(f"{base}/run/{look}")
        role = get(f"{base}/role/told")
        assert "Paints a look and asks the person whether it stays." in role
        assert re.search(r"pick</b></td><td class=\"m\">you</td><td>You choose", role)
    finally:
        server.shutdown()


def test_waiting_on_a_request_returns_once_it_delivered(
        tmp_path: Path, repo: Path, pending: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    out = start(tmp_path, capsys, TWO, repo)
    drain(pending, capsys)

    cli.main(["wait", out["request"], "--home", str(tmp_path / "home")])

    said = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert said["status"] == "delivered" and [r["state"] for r in said["runs"]] == ["delivered", "delivered"]


FOUR = {"request": "A dark look, the home screen in it, polished, and a health page",
        "runs": runs({"name": "look", "role": "tests.planned:LOOK", "task": "Paint a dark look"},
                     {"name": "screen", "role": "tests.planned:SCREEN", "task": "Build the home screen",
                      "after": ["look"]},
                     {"name": "polish", "role": "tests.planned:SCREEN", "task": "Polish the home screen",
                      "after": ["screen"]},
                     {"name": "health", "role": "tests.planned:SCREEN", "task": "Design the health page",
                      "after": ["look"]})}


def test_the_person_drops_a_run_of_the_plan_before_it_starts_and_the_plan_delivers_without_it(
        tmp_path: Path, repo: Path, pending: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_api import call
    from tests.test_review import form, post
    out = start(tmp_path, capsys, FOUR, repo)
    rid = out["request"]
    base, server = serving(tmp_path / "home")
    try:
        page = get(f"{base}/request/{rid}")
        droppable = re.findall(r'name="name" value="([a-z-]+)"><button class="btn"[^>]*>Drop', page)
        assert droppable == ["polish", "health"]  # not started, and no run builds on them
        token = form(page)["token"]
        status, shown = post(f"{base}/drop", {"token": token, "request": rid, "name": "screen"})
        assert status == 400 and "polish builds on it" in shown
        status, _ = call(f"{base}/api/drop-run", {"request": rid, "name": "health"})
        assert status == 403  # an agent never takes a run out of the person's plan
        status, _ = post(f"{base}/drop", {"token": token, "request": rid, "name": "health"})
        assert status == 303
        page = get(f"{base}/request/{rid}")
        assert re.search(r"health: Design the health page.*?dropped", page, re.S)
        assert "0 of 3 runs delivered" in page
    finally:
        server.shutdown()

    assert len(drain(pending, capsys)) == 3  # look, screen and polish; health never starts
    c = Conductor(tmp_path / "home")
    [req] = plan.requests(c, rid)
    assert {x.planned.name: x.state for x in plan.places(c, req)}["health"] == "dropped"
    assert plan.status(c, req) == "delivered"
    with pytest.raises(plan.PlanError, match="already started"):
        plan.drop(c, rid, "look")


def test_a_plan_names_the_specialist_of_each_run_and_the_page_shows_it(
        tmp_path: Path, repo: Path, pending: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    data = {"request": "A dark look, then the home screen in it",
            "runs": runs({"name": "look", "role": "tests.planned:LOOK", "task": "Paint a dark look",
                          "specialist": "brand-designer"},
                         {"name": "screen", "role": "tests.planned:SCREEN", "task": "Build the home screen",
                          "after": ["look"]})}
    out = start(tmp_path, capsys, data, repo)
    c = Conductor(tmp_path / "home")
    [req] = plan.requests(c, out["request"])
    assert [p.specialist for p in req.runs] == ["brand-designer", ""]
    base, server = serving(tmp_path / "home")
    try:
        page = get(f"{base}/request/{out['request']}")
    finally:
        server.shutdown()
    assert re.search(r"look: Paint a dark look.*?brand-designer", page, re.S)
    assert plan.Run.model_json_schema()["properties"]["specialist"]["description"].startswith("who does this run")
