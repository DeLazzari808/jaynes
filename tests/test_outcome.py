"""A stop's count comes from what happened to what it let through: an accept counts as right only once its delivery
held, a correction afterwards counts as an error, and too many errors lately brake the stop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from jaynes import bounds
from jaynes.conductor import AnswerRejected, Conductor, RunResult
from jaynes.gate import Window
from jaynes.outcome import SETTLE
from jaynes.role import role, stop
from tests.fakes import Scripted, write

DAY = 24 * 3600.0


class Note(BaseModel):
    text: str


def payloads(c: Conductor, run_id: str, kind: str) -> list[dict[str, Any]]:
    return [e.payload for e in c.store.events(kind, run_id=run_id)]


def started(c: Conductor, run_id: str) -> list[str]:
    return [p["step"] for p in payloads(c, run_id, "StepStarted")]


def fp(c: Conductor, run_id: str, step: str) -> str:
    return next(p["fingerprint"] for p in payloads(c, run_id, "StopOpened") if p["step"] == step)


def made() -> Any:
    """spec has a stop, build has none, look's stop is the first after build."""
    r = role("made", agent="fake")
    spec = r.step("spec", output=Note, stop=stop(0.3))
    build = r.step("build", output=Note, needs=[spec])
    r.step("look", output=Note, needs=[build], stop=stop(0.3))
    return r


def four() -> Any:
    r = role("four", agent="fake")
    a = r.step("a", output=Note, stop=stop(0.3))
    r.step("b", output=Note, needs=[a])
    c = r.step("c", output=Note)
    r.step("d", output=Note, needs=[c])
    return r


def setup(tmp_path: Path) -> tuple[Conductor, list[float]]:
    now = [1000.0]
    c = Conductor(tmp_path, runtimes={"fake": lambda: Scripted(default=lambda s: {"text": s})},
                  clock=lambda: now[0])
    return c, now


def deliver(c: Conductor, r: Any, run_id: str) -> None:
    c.run(r, "t", run_id)
    for step in [x.name for x in r.steps if x.stop]:
        c.answer(r, run_id, step, "accept", by="person")
    assert c.run(r, "t", run_id).status == "finished"


def test_an_accept_counts_as_right_only_once_its_delivery_held(tmp_path: Path) -> None:
    r = made()
    c, now = setup(tmp_path)
    deliver(c, r, "r1")
    spec = fp(c, "r1", "spec")

    assert c.window(spec) == Window(0, 0)
    assert c.count(spec).pending == 1
    now[0] += SETTLE - 1
    assert c.window(spec) == Window(0, 0)
    now[0] += 1
    assert c.window(spec) == Window(1, 0)
    assert c.count(spec).pending == 0


def test_a_correction_counts_an_error_on_the_first_stop_after_the_step_that_erred(tmp_path: Path) -> None:
    r = made()
    c, now = setup(tmp_path)
    deliver(c, r, "r1")
    spec, look = fp(c, "r1", "spec"), fp(c, "r1", "look")

    p = c.correct(r, "r1", "build", "error", "the endpoint answered 500 when a pull had failed", by="agent")

    assert (p["erred"], p["stop"], p["fingerprint"]) == ("build", "look", look)  # look let build's error through
    assert c.window(look) == Window(1, 1)
    now[0] += 8 * DAY
    assert c.window(look) == Window(1, 1)  # it stays an error
    assert c.window(spec) == Window(1, 0)  # spec did what it was asked
    assert c.correct(r, "r1", "spec", "error", "it asked for the wrong page", by="agent")["stop"] == "spec"
    assert c.window(spec) == Window(1, 1)


def test_a_correction_found_after_the_delivery_held_turns_its_right_into_an_error(tmp_path: Path) -> None:
    r = made()
    c, now = setup(tmp_path)
    deliver(c, r, "r1")
    look = fp(c, "r1", "look")
    now[0] += 30 * DAY
    assert c.window(look) == Window(1, 0)

    c.correct(r, "r1", "look", "error", "the times were in UTC", by="agent")

    assert c.window(look) == Window(1, 1)


def test_a_new_preference_or_new_information_counts_nothing_and_the_person_can_refile_it(tmp_path: Path) -> None:
    r = made()
    c, now = setup(tmp_path)
    deliver(c, r, "r1")
    look = fp(c, "r1", "look")
    now[0] += 8 * DAY

    p = c.correct(r, "r1", "build", "preference", "I want the newest first", by="agent")
    assert c.window(look) == Window(1, 0)

    assert c.reclassify("r1", p["id"], "error", by="person")["was"] == "preference"
    assert c.window(look) == Window(1, 1)
    c.reclassify("r1", p["id"], "info", by="person")
    assert c.window(look) == Window(1, 0)
    [(_, now_is)] = c.corrections("r1")
    assert (now_is["kind"], now_is["by"], now_is["note"]) == ("info", "person", "I want the newest first")


def test_a_correction_needs_a_delivered_run_a_step_of_its_role_a_kind_and_a_note(tmp_path: Path) -> None:
    r = made()
    c, _ = setup(tmp_path)
    assert c.run(r, "t", "open").status == "waiting"
    with pytest.raises(AnswerRejected, match="return it from a stop"):
        c.correct(r, "open", "build", "error", "wrong", by="agent")
    c.dismiss("open")
    with pytest.raises(AnswerRejected, match="delivered nothing"):
        c.correct(r, "open", "build", "error", "wrong", by="agent")
    deliver(c, r, "r1")
    with pytest.raises(AnswerRejected, match="no step 'deploy'"):
        c.correct(r, "r1", "deploy", "error", "wrong", by="agent")
    with pytest.raises(AnswerRejected, match="kind is one of"):
        c.correct(r, "r1", "build", "bug", "wrong", by="agent")
    with pytest.raises(AnswerRejected, match="needs a note"):
        c.correct(r, "r1", "build", "error", "  ", by="agent")
    with pytest.raises(AnswerRejected, match="no correction"):
        c.reclassify("r1", "nope", "error", by="person")


def test_only_what_a_stop_delivered_counts_and_an_error_inside_the_run_counts_at_once(tmp_path: Path) -> None:
    r = role("two", agent="fake")
    a = r.step("a", output=Note, stop=stop(0.3))
    r.step("b", output=Note, needs=[a], stop=stop(0.3))
    now = [1000.0]
    agent = Scripted({("a", 2): write({"text": "short"})}, default=lambda s: {"text": s})
    c = Conductor(tmp_path, runtimes={"fake": lambda: agent}, clock=lambda: now[0])
    assert c.run(r, "t", "r1") == RunResult("waiting", "a")
    c.answer(r, "r1", "b", "accept", by="person")  # b on a's first output
    a_fp, b_fp = fp(c, "r1", "a"), fp(c, "r1", "b")
    c.answer(r, "r1", "a", "return", note="shorter", by="person")
    assert c.window(a_fp) == Window(1, 1)  # counted when it was found

    c.run(r, "t", "r1")  # a again, and b again on it
    c.answer(r, "r1", "a", "accept", by="person")
    c.answer(r, "r1", "b", "accept", by="person")
    assert c.run(r, "t", "r1").status == "finished"
    now[0] += SETTLE

    assert c.window(a_fp) == Window(2, 1)
    assert c.window(b_fp) == Window(1, 0)  # b's first accept was never delivered


def test_a_dismissed_run_counts_only_the_errors_found_in_it(tmp_path: Path) -> None:
    r = made()
    c, now = setup(tmp_path)
    c.run(r, "t", "r1")
    c.answer(r, "r1", "spec", "accept", by="person")
    c.answer(r, "r1", "look", "return", note="the colors are off", by="person")
    c.dismiss("r1")
    now[0] += SETTLE

    assert c.window(fp(c, "r1", "spec")) == Window(0, 0)
    assert c.window(fp(c, "r1", "look")) == Window(1, 1)


def test_an_agent_release_earns_no_right_but_a_correction_still_counts(tmp_path: Path) -> None:
    r = made()
    c, now = setup(tmp_path)
    c.run(r, "t", "r1")
    c.answer(r, "r1", "spec", "accept", by="agent")
    c.answer(r, "r1", "look", "accept", by="person")
    assert c.run(r, "t", "r1").status == "finished"
    spec = fp(c, "r1", "spec")
    now[0] += SETTLE
    assert (c.window(spec), c.count(spec).pending) == (Window(0, 0), 0)

    c.correct(r, "r1", "spec", "error", "it read the wrong file", by="agent")

    assert c.window(spec) == Window(1, 1)


def brake(c: Conductor, r: Any) -> None:
    """Two runs whose stop at a was returned: two errors in the last ten."""
    for run_id in ("w0", "w1"):
        c.run(r, "t", run_id)
        c.answer(r, run_id, "a", "return", note="wrong", by="person")
        c.dismiss(run_id)


def test_two_errors_in_the_last_ten_brake_the_stop_so_it_asks_and_holds_the_run(tmp_path: Path) -> None:
    r = four()
    c, _ = setup(tmp_path)
    c.run(r, "t", "w0")
    c.answer(r, "w0", "a", "return", note="wrong", by="person")
    c.dismiss("w0")
    assert c.run(r, "t", "one") == RunResult("waiting", "a")
    assert payloads(c, "one", "StopOpened")[0]["status"] == "undetermined"
    assert started(c, "one") == ["a", "b", "c", "d"]  # one error does not brake it
    c.dismiss("one")
    c.run(r, "t", "w1")
    c.answer(r, "w1", "a", "return", note="wrong", by="person")
    c.dismiss("w1")

    assert c.run(r, "t", "x") == RunResult("waiting", "a")

    [opened] = payloads(c, "x", "StopOpened")
    assert opened["status"] == "braked" and opened["holds"] is True
    assert opened["braked"]["n"] == 0
    assert started(c, "x") == ["a"]  # the run waits for the person at the braked stop
    with pytest.raises(AnswerRejected, match="braked"):
        c.answer(r, "x", "a", "accept", by="agent")
    c.answer(r, "x", "a", "accept", by="person")
    assert c.run(r, "t", "x").status == "finished"
    assert started(c, "x") == ["a", "b", "c", "d"]


def test_a_braked_stop_is_released_only_by_the_outcomes_after_the_brake(tmp_path: Path) -> None:
    r = four()
    c, now = setup(tmp_path)
    brake(c, r)
    need = bounds.labels_to_certify(0, 0.3, 0.05)
    assert need == 13
    for i in range(need - 1):
        deliver(c, r, f"ok{i}")
    now[0] += SETTLE
    assert c.run(r, "t", "still") == RunResult("waiting", "a")
    assert payloads(c, "still", "StopOpened")[0]["status"] == "braked"
    assert payloads(c, "still", "StopOpened")[0]["braked"]["n"] == need - 1
    c.answer(r, "still", "a", "accept", by="person")
    c.run(r, "t", "still")
    now[0] += SETTLE

    assert c.run(r, "t", "free") == RunResult("waiting", "a")

    [opened] = payloads(c, "free", "StopOpened")
    assert opened["status"] != "braked" and "holds" not in opened
    assert started(c, "free") == ["a", "b", "c", "d"]


def test_while_a_braked_stop_holds_the_run_a_later_stop_built_on_the_returned_work_closes(tmp_path: Path) -> None:
    r = role("two", agent="fake")
    a = r.step("a", output=Note, stop=stop(0.3))
    r.step("b", output=Note, needs=[a], stop=stop(0.3))
    agent = Scripted({("a", 3): write({"text": "short"})}, default=lambda s: {"text": s})
    c = Conductor(tmp_path, runtimes={"fake": lambda: agent})
    c.run(r, "t", "w0")
    c.answer(r, "w0", "a", "return", note="wrong", by="person")
    c.dismiss("w0")
    assert c.run(r, "t", "x") == RunResult("waiting", "a")
    assert [w.step for w in c.queue() if w.run_id == "x"] == ["a", "b"]

    c.answer(r, "x", "a", "return", note="shorter", by="person")  # the second error: a is braked
    assert c.run(r, "t", "x") == RunResult("waiting", "a")

    assert payloads(c, "x", "StopOpened")[-1]["status"] == "braked"
    assert [w.step for w in c.queue() if w.run_id == "x"] == ["a"]  # b's stop showed work on the returned a
    c.answer(r, "x", "a", "accept", by="person")
    assert c.run(r, "t", "x") == RunResult("waiting", "b")
    assert started(c, "x") == ["a", "b", "a", "b"]
