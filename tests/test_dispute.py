"""An agent says an earlier step's output stands in its way: the agents settle it, and the person only if they cannot."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from jaynes import api, cli
from jaynes.conductor import Conductor
from jaynes.role import DISPUTES, Disputes, Pass, Witnesses, role
from tests.fakes import Scripted, write
from tests.test_api import call
from tests.test_review import get, post, serving

SAID = "test_zero expects the old total"


class Note(BaseModel):
    text: str


def works(out: Note, _: Witnesses) -> Pass:
    if out.text.startswith("wrong: "):
        raise Disputes("tests", out.text.removeprefix("wrong: "), about="test")
    return Pass()


R = role("dispute", agent="fake")
TESTS = R.step("tests", output=Note)
R.step("code", output=Note, needs=[TESTS], check=works)


def stubborn(step: str) -> dict[str, str]:
    return {"text": "t"} if step == "tests" else {"text": "wrong: " + SAID}


def Stubborn() -> Scripted:
    return Scripted(default=stubborn)


def form_of(page: str, button: str) -> dict[str, str]:
    """The hidden fields of the form whose button says ``button``."""
    found = [f for f in re.findall(r"<form.*?</form>", page, re.S) if f">{button}</button>" in f]
    assert len(found) == 1, f"{len(found)} forms with {button}"
    return {k: html.unescape(v) for k, v in re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', found[0])}


@pytest.fixture
def blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Conductor, str]:
    """A run whose agents still disagree, started as the page starts one, with no process continuing it."""
    def nothing(home: Path, run_id: str) -> None:
        pass

    monkeypatch.setattr(api, "continue_in_background", nothing)  # the page's buttons go through the API
    cli.main(["run", "--home", str(tmp_path), "--role", "tests.test_dispute:R", "--task", "t",
              "--runtime", "fake=tests.test_dispute:Stubborn"])
    c = Conductor(tmp_path)
    [run] = c.store.runs()
    return c, run


def test_the_step_that_made_it_runs_again_with_what_the_agent_said_and_the_person_is_not_called(
        tmp_path: Path) -> None:
    agent = Scripted({("tests", 1): write({"text": "t1"}), ("code", 1): write({"text": "wrong: " + SAID}),
                      ("tests", 2): write({"text": "t2"}), ("code", 2): write({"text": "done"})})
    c = Conductor(tmp_path, {"fake": lambda: agent})

    assert c.run(R, "t", "r1").status == "finished"

    assert SAID in [p for p in agent.prompts if "# Step: tests\n" in p][-1]
    assert not list(c.store.events("RunBlocked", run_id="r1"))
    [back] = [e.payload for e in c.store.events("StepReturned", run_id="r1")]
    assert (back["step"], back["to"], back["reasons"]) == ("code", "tests", [SAID])


def test_when_the_agents_still_disagree_the_person_chooses_in_plain_words(tmp_path: Path) -> None:
    c = Conductor(tmp_path, {"fake": lambda: Scripted(default=stubborn)})

    blocked = c.run(R, "t", "r1")

    assert (blocked.status, blocked.step) == ("blocked", "code")
    assert len(list(c.store.events("StepReturned", run_id="r1"))) == DISPUTES
    assert list(blocked.reasons) == ["Two agents disagree about a test: the one at code says it is wrong, and the one at "
                               "tests kept it twice. Fix the test, or keep it."]
    [payload] = [e.payload for e in c.store.events("RunBlocked", run_id="r1")]
    assert payload["asks"] is True and payload["dispute"] == {"to": "tests", "about": "test", "said": SAID}


def test_the_person_keeps_the_test_with_one_button(tmp_path: Path, blocked: tuple[Conductor, str]) -> None:
    c, run = blocked
    base, server = serving(tmp_path)
    try:
        page = get(f"{base}/stop/{run}/code")
        assert "Two agents disagree about a test" in page and "Fix the test" in page and "Keep the test" in page
        assert "What was wrong?" not in page  # nothing to write

        status, _ = post(base + "/send-back", form_of(page, "Keep the test"))

        assert status == 303
        [sent] = [e.payload for e in c.store.events("RunSentBack", run_id=run)]
        assert (sent["to"], sent["by"]) == ("code", "person") and "make your work pass it" in sent["note"]
    finally:
        server.shutdown()


def test_fix_the_test_sends_back_what_the_agent_said(tmp_path: Path, blocked: tuple[Conductor, str]) -> None:
    c, run = blocked
    base, server = serving(tmp_path)
    try:
        status, _ = post(base + "/send-back", form_of(get(f"{base}/stop/{run}/code"), "Fix the test"))

        assert status == 303
        [sent] = [e.payload for e in c.store.events("RunSentBack", run_id=run)]
        assert (sent["to"], sent["by"]) == ("tests", "person") and SAID in sent["note"]
    finally:
        server.shutdown()


def test_an_agent_sees_the_dispute_and_the_two_choices_the_page_gives(tmp_path: Path,
                                                                      blocked: tuple[Conductor, str]) -> None:
    c, run = blocked
    base, server = serving(tmp_path)
    try:
        page = get(f"{base}/stop/{run}/code")
        [w] = json.loads(get(base + "/api/home"))["waiting"]
        fix, keep = w["dispute"]["choices"]
        status, out = call(base + "/api/send-back", {"run": run, "to": keep["to"], "note": keep["note"]})
    finally:
        server.shutdown()
    assert (w["kind"], w["who_answers"]) == ("blocked", "anyone")
    assert (w["dispute"]["about"], w["dispute"]["said"]) == ("test", SAID)
    assert [x["label"] for x in (fix, keep)] == ["Fix the test", "Keep the test"]
    for x in (fix, keep):  # the same send back the page's button posts
        assert {k: form_of(page, x["label"])[k] for k in ("to", "note")} == {"to": x["to"], "note": x["note"]}
    assert status == 200, out
    [sent] = [e.payload for e in c.store.events("RunSentBack", run_id=run)]
    assert (sent["to"], sent["by"]) == ("code", "agent")
