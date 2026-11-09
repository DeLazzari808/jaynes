"""Halting everything: no run takes a step or merges until a person resumes; anyone may halt."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel

from jaynes import cli
from jaynes.conductor import AnswerRejected, Conductor, RunResult
from jaynes.role import Role, role, stop
from tests.fakes import Scripted
from tests.test_review import form, get, post, serving


class Note(BaseModel):
    text: str


def two() -> Role:
    r = role("two", agent="fake")
    a = r.step("a", output=Note, stop=stop(0.2))
    r.step("b", output=Note, needs=[a])
    return r


def test_a_halt_stops_every_run_before_its_next_step_until_a_person_resumes(tmp_path: Path) -> None:
    r = two()
    c = Conductor(tmp_path, runtimes={"fake": lambda: Scripted(default=lambda s: {"text": s})})
    c.run(r, "t", "r1")
    c.halt(by="agent", note="something looks off")

    assert c.run(r, "t", "r2") == RunResult("halted")
    assert not list(c.store.events("StepStarted", run_id="r2"))
    c.answer(r, "r1", "a", "accept", by="person")  # answers are still recorded
    assert c.run(r, "t", "r1") == RunResult("halted")
    assert (c.halted() or {})["note"] == "something looks off"
    with pytest.raises(AnswerRejected, match="only a person resumes"):
        c.resume(by="agent")

    assert sorted(c.resume(by="person")) == ["r1", "r2"]

    assert c.halted() is None
    assert c.run(r, "t", "r1").status == "finished"
    with pytest.raises(AnswerRejected, match="nothing is halted"):
        c.resume(by="person")


def test_nothing_merges_while_halted(tmp_path: Path) -> None:
    from tests.merging import ROLE, Agent
    main = tmp_path / "repo"
    main.mkdir()
    (main / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "start"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=main, check=True)
    c = Conductor(tmp_path / "home", runtimes={"quick": lambda: Agent()})
    c.halt(by="person")
    assert c.run(ROLE, "add should add", "r1", project=main) == RunResult("halted")
    c.resume(by="person")
    assert c.run(ROLE, "add should add", "r1").status == "waiting"
    assert [e.payload["into"] for e in c.store.events("Merged", run_id="r1")] == ["main"]


def test_an_agent_halts_from_the_terminal_and_the_person_resumes_from_the_page(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["halt", "--home", str(tmp_path), "--note", "the tests fail on main"])
    assert json.loads(capsys.readouterr().out)["halted"] is True
    base, server = serving(tmp_path)
    try:
        page = get(base + "/")
        assert "Everything is halted." in page and "Halt all" not in page

        status, _ = post(base + "/resume", form(page))

        assert status == 303
        page = get(base + "/")
        assert "Everything is halted." not in page and "Halt all" in page
        status, _ = post(base + "/halt", form(page))
        assert status == 303 and (Conductor(tmp_path).halted() or {})["by"] == "person"
    finally:
        server.shutdown()
