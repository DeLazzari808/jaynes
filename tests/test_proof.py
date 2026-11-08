"""Proof the maker cannot see: cases written from the request alone before the work, run by Jaynes against it; the
maker hears only the sentence of a case that fails."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from jaynes import proof
from jaynes.conductor import Conductor, RunResult
from jaynes.role import Back, Checked, StepContext, Witnesses, role, stop
from tests.fakes import Scripted, write
from tests.test_fix_bug import edit
from tests.test_fix_bug import repo  # noqa: F401  # pyright: ignore[reportUnusedImport]  # a fixture

ADDS = f"{sys.executable} -c 'from calc import add; assert add(2, 2) == 4'"
SERVED: dict[str, Any] = {"command": f"{sys.executable} -m http.server {{port}} --bind 127.0.0.1", "path": "/",
                          "ports": []}


class Made(BaseModel):
    summary: str


def proved_role() -> tuple[Any, Any]:
    r = role("proved", agent="fake")
    cases = r.step("cases", output=proof.Cases, project="read", hidden=True, check=proof.written,
                   instructions=proof.WRITE)
    make = r.step("make", output=Made, project="write", may_change=["**"])
    prove = r.step("prove", output=proof.Proved, needs=[cases, make], run_by=proof.prove, project="read",
                   effect="read", max_turns=1, back=Back(make, proof.failed))
    r.step("accept", needs=[prove, make], stop=stop(0.05, sampled=False))
    return r, prove


def test_a_hidden_step_reads_only_the_request_before_the_work_and_no_agent_receives_it() -> None:
    r = role("h", agent="fake")
    first = r.step("first", output=Made)
    with pytest.raises(ValueError, match="reads only the request"):
        r.step("cases", output=proof.Cases, needs=[first], hidden=True)
    cases = r.step("cases", output=proof.Cases, project="read", hidden=True)
    make = r.step("make", output=Made, project="write", may_change=["**"])
    with pytest.raises(ValueError, match="may not receive the hidden cases"):
        r.step("peek", output=Made, needs=[cases])
    with pytest.raises(ValueError, match="comes before the work; make change the project"):
        r.step("late", output=proof.Cases, hidden=True)
    r.step("prove", output=proof.Proved, needs=[cases, make], run_by=proof.prove, project="read")
    r.step("accept", needs=[cases], stop=stop(0.2))  # the person sees them


def test_a_failing_case_sends_the_maker_back_with_its_sentence_alone(repo: Path, tmp_path: Path) -> None:
    r, prove = proved_role()
    agent = Scripted({
        ("cases", 1): write({"cases": [{"says": "Adding 2 and 2 gives 4", "run": ADDS}], "untestable": []}),
        ("make", 1): edit({"calc.py": "def add(a, b):\n    return a * 3\n"}, {"summary": "tried"}),
        ("make", 2): edit({"calc.py": "def add(a, b):\n    return a + b\n"}, {"summary": "adds"}),
    })
    c = Conductor(tmp_path / "home", runtimes={"fake": lambda: agent})

    assert c.run(r, "add should add", "r1", project=repo) == RunResult("waiting", "accept")

    [back] = [e.payload for e in c.store.events("StepReturned", run_id="r1")]
    assert back["to"] == "make"
    assert back["reasons"] == ["A check you cannot see found that this does not hold: Adding 2 and 2 gives 4"]
    again = [p for p in agent.prompts if "# Step: make\n" in p][-1]
    assert "Adding 2 and 2 gives 4" in again
    assert not any("assert add(2, 2)" in p for p in agent.prompts if "# Step: make\n" in p)  # never the command
    for ws in (tmp_path / "home" / "runs" / "r1" / "make").glob("turn-*/workspace"):
        assert not (ws / "inputs" / "cases.json").exists()
    done = c.output("r1", prove)
    assert done is not None and done.passed and [x.says for x in done.results] == ["Adding 2 and 2 gives 4"]


def test_after_two_rounds_the_run_goes_on_and_the_stop_shows_what_still_fails(repo: Path, tmp_path: Path) -> None:
    r, prove = proved_role()
    wrong = {("make", n): edit({"calc.py": f"def add(a, b):\n    return a * {n + 2}\n"}, {"summary": f"try {n}"})
             for n in (1, 2, 3)}
    agent = Scripted({("cases", 1): write({"cases": [{"says": "Adding 2 and 2 gives 4", "run": ADDS}]}), **wrong})
    c = Conductor(tmp_path / "home", runtimes={"fake": lambda: agent})

    assert c.run(r, "add should add", "r1", project=repo) == RunResult("waiting", "accept")

    assert agent.counts["make"] == 3  # the first try and two rounds back
    done = c.output("r1", prove)
    assert done is not None and not done.passed
    shown = proof.shown(done)
    assert shown.title == "0 of 1 hidden checks pass" and "Fails: Adding 2 and 2 gives 4" in shown.text


def test_a_case_against_the_app_runs_on_the_served_change_and_is_not_run_without_a_preview(
        repo: Path, tmp_path: Path) -> None:
    project = tmp_path / "copy"
    project.mkdir()
    (project / "hello.txt").write_text("Hello there\n")
    cases = proof.Cases(cases=[proof.Case(says="The app greets", run='curl -s "$JAYNES_URL/hello.txt" | grep -qi hello'),
                               proof.Case(says="The app has no bye page",
                                          run='curl -s -o /dev/null -w "%{http_code}" "$JAYNES_URL/bye" | grep -qx 404')])
    needs = Witnesses({"cases": Checked("cases", cases, "h", "c")}, project, source=repo)
    work = tmp_path / "turn"
    work.mkdir()

    served = proof.prove(StepContext("r1", "t", needs, work, started={"preview": SERVED}))

    assert served.served and served.passed
    unserved = proof.prove(StepContext("r1", "t", needs, work, started={}))
    assert not unserved.served and not unserved.passed
    assert [(x.ran, x.passed) for x in unserved.results] == [(False, False), (False, False)]
    assert proof.failed(unserved, needs) == []  # the maker is not sent back for what could not run


def test_nothing_testable_from_outside_is_no_proof() -> None:
    out = proof.Proved(results=[], untestable=["the screen looks calm"])
    assert not out.passed
    assert proof.shown(out).text.startswith("Nothing the request asks can be checked from outside: the screen")
