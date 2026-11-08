"""The person on the loop: a stop shows the output without holding the run, and a return corrects on top, running
again only the steps whose inputs changed."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from jaynes.conductor import AnswerRejected, Conductor, RunResult
from jaynes.gate import Window
from jaynes.role import StepContext, role, stop
from tests.fakes import Action, Scripted, quota, write


class Note(BaseModel):
    text: str


def kinds(c: Conductor, run_id: str) -> list[str]:
    return [e.type for e in c.store.events(run_id=run_id)]


def payloads(c: Conductor, run_id: str, kind: str) -> list[dict[str, Any]]:
    return [e.payload for e in c.store.events(kind, run_id=run_id)]


def started(c: Conductor, run_id: str) -> list[str]:
    return [p["step"] for p in payloads(c, run_id, "StepStarted")]


def stops(c: Conductor, run_id: str) -> list[str]:
    return [w.step for w in c.queue() if w.run_id == run_id and w.kind == "stop"]


def four() -> Any:
    """a has a stop and b needs it; c needs nothing and d needs c, so a return of a changes nothing of c and d."""
    r = role("four", agent="fake")
    a = r.step("a", output=Note, stop=stop(0.2))
    r.step("b", output=Note, needs=[a])
    c = r.step("c", output=Note)
    r.step("d", output=Note, needs=[c])
    return r


def test_a_stop_shows_the_output_and_the_run_goes_on_to_the_end(tmp_path: Path) -> None:
    r = four()
    c = Conductor(tmp_path, runtimes={"fake": lambda: Scripted(default=lambda s: {"text": s})})

    assert c.run(r, "t", "r1") == RunResult("waiting", "a")

    assert started(c, "r1") == ["a", "b", "c", "d"]  # every step ran before the person answered
    assert "RunFinished" not in kinds(c, "r1")  # it waits for the answer, holding nothing
    assert stops(c, "r1") == ["a"]
    c.answer(r, "r1", "a", "accept", by="person")
    assert c.run(r, "t", "r1").status == "finished"
    assert started(c, "r1") == ["a", "b", "c", "d"]


def test_every_stop_of_a_run_waits_for_the_person_at_once(tmp_path: Path) -> None:
    r = role("two", agent="fake")
    outline = r.step("outline", output=Note, stop=stop(0.2))
    r.step("draft", output=Note, needs=[outline], stop=stop(0.2))
    c = Conductor(tmp_path, runtimes={"fake": lambda: Scripted(default=lambda s: {"text": s})})

    assert c.run(r, "t", "r1") == RunResult("waiting", "outline")
    assert stops(c, "r1") == ["outline", "draft"]
    c.answer(r, "r1", "draft", "accept", by="person")  # in any order
    assert c.run(r, "t", "r1") == RunResult("waiting", "outline")
    c.answer(r, "r1", "outline", "accept", by="person")
    assert c.run(r, "t", "r1").status == "finished"


def test_a_return_runs_again_only_the_steps_whose_inputs_changed(tmp_path: Path) -> None:
    r = four()
    agent = Scripted({("a", 1): write({"text": "one"}), ("a", 2): write({"text": "two"})},
                     default=lambda s: {"text": s})
    c = Conductor(tmp_path, runtimes={"fake": lambda: agent})
    c.run(r, "t", "r1")

    c.answer(r, "r1", "a", "return", note="say two", by="person")
    assert c.run(r, "t", "r1") == RunResult("waiting", "a")

    assert started(c, "r1") == ["a", "b", "c", "d", "a", "b"]  # c and d received nothing new
    [changed] = payloads(c, "r1", "InputsChanged")
    assert (changed["step"], changed["changed"]) == ("b", ["a"])
    b_again = agent.prompts[-1]
    assert "inputs/previous.json" in b_again and "a" in b_again and "changed" in b_again
    c.answer(r, "r1", "a", "accept", by="person")
    assert c.run(r, "t", "r1").status == "finished"


def test_a_rerun_that_gives_the_same_output_runs_nothing_after_it(tmp_path: Path) -> None:
    r = four()
    c = Conductor(tmp_path, runtimes={"fake": lambda: Scripted(default=lambda s: {"text": s})})
    c.run(r, "t", "r1")
    c.answer(r, "r1", "a", "return", note="look again", by="person")
    c.run(r, "t", "r1")
    assert started(c, "r1") == ["a", "b", "c", "d", "a"]
    assert payloads(c, "r1", "InputsChanged") == []


def test_a_later_stop_closes_when_its_inputs_change_and_opens_again_on_the_new_output(tmp_path: Path) -> None:
    r = role("two", agent="fake")
    outline = r.step("outline", output=Note, stop=stop(0.2))
    draft = r.step("draft", output=Note, needs=[outline], stop=stop(0.2))
    agent = Scripted({("outline", 2): write({"text": "better outline"}), ("draft", 2): write({"text": "new draft"})},
                     default=lambda s: {"text": s})
    c = Conductor(tmp_path, runtimes={"fake": lambda: agent})
    c.run(r, "t", "r1")
    old = next(w.evidence for w in c.queue() if w.step == "draft")

    c.answer(r, "r1", "outline", "return", note="better", by="person")
    c.run(r, "t", "r1")

    assert stops(c, "r1") == ["outline", "draft"]
    with pytest.raises(AnswerRejected, match="changed"):
        c.answer(r, "r1", "draft", "accept", by="person", evidence=old)
    assert c.output("r1", draft) == Note(text="new draft")


def test_a_return_to_an_earlier_step_whose_stop_is_open_counts_an_error_there(tmp_path: Path) -> None:
    r = role("two", agent="fake")
    outline = r.step("outline", output=Note, stop=stop(0.2))
    r.step("draft", output=Note, needs=[outline], stop=stop(0.2))
    c = Conductor(tmp_path, runtimes={"fake": lambda: Scripted(default=lambda s: {"text": s})})
    c.run(r, "t", "r1")
    first = {p["step"]: p["fingerprint"] for p in payloads(c, "r1", "StopOpened")}

    c.answer(r, "r1", "draft", "return", note="the outline misses a part", to="outline", by="person")

    assert c.window(first["outline"]) == Window(1, 1)
    assert c.window(first["draft"]) == Window(0, 0)  # draft did what it was given


def test_an_irreversible_step_waits_for_every_stop_before_it(tmp_path: Path) -> None:
    acted: list[str] = []

    def send(ctx: StepContext) -> Note:
        acted.append(ctx.run_id)
        return Note(text="sent")

    r = role("send", agent="fake")
    message = r.step("message", output=Note, stop=stop(0.05, sampled=False))
    r.step("send", output=Note, needs=[message], run_by=send, effect="irreversible")
    c = Conductor(tmp_path, runtimes={"fake": lambda: Scripted(default=lambda s: {"text": s})})

    assert c.run(r, "t", "r1") == RunResult("waiting", "message")
    assert acted == []
    c.answer(r, "r1", "message", "accept", by="person")
    assert c.run(r, "t", "r1").status == "finished"
    assert acted == ["r1"]


def test_an_answer_is_recorded_while_a_process_holds_the_run(tmp_path: Path) -> None:
    r = four()
    c = Conductor(tmp_path, runtimes={"fake": lambda: Scripted(default=lambda s: {"text": s})})
    c.run(r, "t", "r1")
    with c._lock("r1"):  # pyright: ignore[reportPrivateUsage]
        c.answer(r, "r1", "a", "accept", by="person")
    assert c.run(r, "t", "r1").status == "finished"


def test_a_run_paused_for_quota_with_a_stop_open_is_due_after_the_reset(tmp_path: Path) -> None:
    now = [1000.0]
    r = four()
    agent = Scripted({("b", 1): quota(2000.0)}, default=lambda s: {"text": s})
    c = Conductor(tmp_path, runtimes={"fake": lambda: agent}, clock=lambda: now[0])
    assert c.run(r, "t", "r1").status == "paused"
    assert stops(c, "r1") == ["a"]
    now[0] = 2001.0
    assert c.due() == [("r1", 2000.0)]


# the project ------------------------------------------------------------------

def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "src").mkdir(parents=True)
    (r / "src" / "app.ts").write_text("export const x = 1\n")
    (r / "src" / "other.ts").write_text("export const y = 1\n")
    git(r, "init", "-q")
    git(r, "add", "-A")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start")
    return r


def edit(path: str, text: str, value: Any) -> Action:
    def act(ws: Path) -> None:
        (ws / "project" / path).write_text(text)
        write(value)(ws)
    return act


def test_a_returned_step_corrects_on_top_of_its_earlier_change(repo: Path, tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def second(ws: Path) -> None:  # sees its first change applied, and adds to it
        seen["app"] = (ws / "project" / "src" / "app.ts").read_text()
        seen["patch"] = (ws / "inputs" / "previous.patch").read_text()
        edit("src/other.ts", "export const y = 2\n", {"text": "both"})(ws)

    r = role("code", agent="fake")
    r.step("change", output=Note, project="write", may_change=["src/**"], stop=stop(0.2))
    agent = Scripted({("change", 1): edit("src/app.ts", "export const x = 2\n", {"text": "x"}),
                      ("change", 2): second})
    c = Conductor(tmp_path / "home", runtimes={"fake": lambda: agent})
    c.run(r, "t", "r1", project=repo)

    c.answer(r, "r1", "change", "return", note="also y", by="person")
    c.run(r, "t", "r1")

    assert seen["app"] == "export const x = 2\n"
    assert "export const x = 2" in seen["patch"]
    assert "already applied" in agent.prompts[-1]
    last = payloads(c, "r1", "Checked")[-1]
    assert last["changed"] == ["src/app.ts", "src/other.ts"]  # the step's change is the earlier one and the fix


def test_the_branch_is_made_before_the_answers_and_follows_a_correction(repo: Path, tmp_path: Path) -> None:
    r = role("code", agent="fake")
    r.step("change", output=Note, project="write", may_change=["src/**"], stop=stop(0.2))
    agent = Scripted({("change", 1): edit("src/app.ts", "export const x = 2\n", {"text": "x"}),
                      ("change", 2): edit("src/app.ts", "export const x = 3\n", {"text": "x"})})
    c = Conductor(tmp_path / "home", runtimes={"fake": lambda: agent})

    assert c.run(r, "t", "r1", project=repo) == RunResult("waiting", "change")
    assert git(repo, "show", "jaynes/r1:src/app.ts") == "export const x = 2"
    c.answer(r, "r1", "change", "return", note="three", by="person")
    assert c.run(r, "t", "r1") == RunResult("waiting", "change")
    assert git(repo, "show", "jaynes/r1:src/app.ts") == "export const x = 3"
    assert len(payloads(c, "r1", "BranchCreated")) == 2
    c.answer(r, "r1", "change", "accept", by="person")
    assert c.run(r, "t", "r1").status == "finished"
    assert len(payloads(c, "r1", "BranchCreated")) == 2  # nothing changed since


def test_a_change_to_paths_that_run_outside_the_box_holds_the_run(repo: Path, tmp_path: Path) -> None:
    r = role("code", agent="fake")
    change = r.step("change", output=Note, project="write", may_change=["**"], stop=stop(0.2))
    r.step("after", output=Note, needs=[change])
    agent = Scripted({("change", 1): edit("Makefile", "all:\n", {"text": "x"})}, default=lambda s: {"text": s})
    c = Conductor(tmp_path / "home", runtimes={"fake": lambda: agent})

    assert c.run(r, "t", "r1", project=repo) == RunResult("waiting", "change")
    assert started(c, "r1") == ["change"]


def test_a_step_that_needs_a_stop_with_no_output_runs_once_the_person_answered_it(tmp_path: Path) -> None:
    heard: list[list[str]] = []

    def listen(ctx: StepContext) -> Note:
        heard.append([n.note for n in ctx.history.notes])
        return Note(text="heard")

    r = role("learn", agent="fake")
    make = r.step("make", output=Note)
    accept = r.step("accept", needs=[make], stop=stop(0.05, sampled=False))
    r.step("notes", output=Note, needs=[accept], run_by=listen)
    agent = Scripted(default=lambda s: {"text": s})
    c = Conductor(tmp_path, runtimes={"fake": lambda: agent})

    assert c.run(r, "t", "r1") == RunResult("waiting", "accept")
    assert heard == []  # all such a stop passes on is the answer, and there is none yet
    c.answer(r, "r1", "accept", "accept", note="warmer next time", by="person")
    assert c.run(r, "t", "r1").status == "finished"
    assert heard == [["warmer next time"]]


def test_a_run_blocked_while_an_earlier_stop_waits_tries_again_when_the_answer_changes_its_inputs(
        tmp_path: Path) -> None:
    from jaynes.role import Fail, Pass

    r = role("spec", agent="fake")
    spec = r.step("spec", output=Note, stop=stop(0.2))
    r.step("make", output=Note, needs=[spec], max_turns=2,
           check=lambda out, needs: Fail("the spec asks for 4 of 4") if "4 of 4" in needs["spec"].value.text
           else Pass())
    agent = Scripted({("spec", 1): write({"text": "4 of 4"}), ("spec", 2): write({"text": "3 of 4"})},
                     default=lambda s: {"text": "built"})
    c = Conductor(tmp_path, runtimes={"fake": lambda: agent})

    assert c.run(r, "t", "r1").status == "blocked"  # before the person looked at the spec
    before = len(kinds(c, "r1"))
    assert c.run(r, "t", "r1").status == "blocked" and len(kinds(c, "r1")) == before  # nothing it receives changed
    c.answer(r, "r1", "spec", "return", note="he ranks 3 of 4", by="person")
    assert c.run(r, "t", "r1") == RunResult("waiting", "spec")
    assert [p["unblocks"] for p in payloads(c, "r1", "InputsChanged")] == [True]
    assert c.output("r1", r.steps[1]) == Note(text="built")
