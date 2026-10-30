"""A stop asked in the terminal: Claude Code's question dialog shows it, and its hook records the person's answer."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from jaynes import cli, skill
from jaynes.conductor import Conductor
from jaynes.store import Store


def started(home: Path, role: str = "tests.stopped:ROLE", agent: str = "quick=tests.stopped:Agent",
            *more: str) -> str:
    cli.main(["run", "--home", str(home), "--role", role, "--task", "t", "--runtime", agent, *more])
    [run] = Store(home / "jaynes.sqlite3").runs()
    return run


def asked(home: Path, run: str, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    capsys.readouterr()
    cli.main(["ask", run, "--home", str(home)])
    return json.loads(capsys.readouterr().out)


def hook(shown: dict[str, Any], answer: str, notes: str = "") -> str | None:
    """Claude Code runs the hook with the dialog's input and what the person chose."""
    text = shown["questions"][0]["question"]
    answers = {text: answer}
    annotations = {text: {"notes": notes}} if notes else {}
    event = {"hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
             "tool_input": {**shown, "answers": answers, "annotations": annotations},
             "tool_response": {**shown, "answers": answers, "annotations": annotations}}
    out = io.StringIO()
    assert cli.answered(io.StringIO(json.dumps(event)), out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"] if out.getvalue() else None


def answers(home: Path, run: str) -> list[dict[str, Any]]:
    return [e.payload for e in Store(home / "jaynes.sqlite3").events("StopAnswered", run_id=run)]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("JAYNES_HOME", str(tmp_path))  # where Claude Code's hook looks
    skill.install()
    return tmp_path


def test_an_accept_in_the_dialog_is_the_persons_and_the_run_goes_on(
        home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = started(home, "tests.stopped:ROLE", "quick=tests.stopped:Agent", "--here")
    shown = asked(home, run, capsys)
    [q] = shown["questions"]
    assert q["header"] == "Jaynes" and q["question"] == f"Ship 'draft one'?\n\n[run {run}, step draft]"
    assert [o["label"] for o in q["options"]] == ["Accept", "Return"]
    assert "text: draft one" in q["options"][0]["preview"]
    said = hook(shown, "Accept")
    assert said is not None and "recorded the person's answer at draft: accept" in said and f"jaynes run {run}" in said
    [a] = answers(home, run)
    assert (a["by"], a["via"], a["label"]) == ("person", "terminal", "correct")
    capsys.readouterr()
    cli.main(["run", run, "--home", str(home)])
    assert json.loads(capsys.readouterr().out)["status"] == "finished"


def test_a_question_the_agent_changed_records_nothing(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = started(home, "tests.stopped:ROLE", "quick=tests.stopped:Agent", "--here")
    shown = asked(home, run, capsys)
    shown["questions"][0]["options"][0]["preview"] = "text: something else"
    said = hook(shown, "Accept")
    assert said is not None and "recorded nothing" in said and "differs" in said
    assert answers(home, run) == []


def test_a_return_needs_a_note_and_words_of_ones_own_are_a_return(
        home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = started(home, "tests.stopped:ROLE", "quick=tests.stopped:Agent", "--here")
    shown = asked(home, run, capsys)
    said = hook(shown, "Return")
    assert said is not None and "needs a note" in said and answers(home, run) == []
    hook(shown, "say it in fewer words")
    [a] = answers(home, run)
    assert (a["answer"], a["note"], a["by"]) == ("return", "say it in fewer words", "person")


def test_a_return_with_a_note_at_a_stop_with_no_output_goes_to_the_step_before(
        home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = started(home, "tests.two_stops:PURE", "quick=tests.two_stops:Agent", "--here")
    shown = asked(home, run, capsys)
    assert "From write:\ntext: words" in shown["questions"][0]["options"][0]["preview"]
    hook(shown, "Return", notes="shorter")
    [a] = answers(home, run)
    assert (a["answer"], a["note"], a["to"]) == ("return", "shorter", "write")


def test_a_floor_stop_is_answered_in_the_dialog_because_the_person_answers_it(
        home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = started(home, "tests.two_stops:FLOOR", "quick=tests.two_stops:Agent", "--here")
    hook(asked(home, run, capsys), "Accept", notes="looks right")
    [a] = answers(home, run)
    assert (a["answer"], a["note"], a["by"]) == ("accept", "looks right", "person")


def test_a_dialog_that_is_not_jaynes_is_left_alone_and_a_broken_event_breaks_nothing(home: Path) -> None:
    other = {"questions": [{"question": "Which color?", "header": "Color", "multiSelect": False,
                            "options": [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}]}]}
    assert hook(other, "Red") is None
    out = io.StringIO()
    assert cli.answered(io.StringIO("not json"), out) == 0
    assert "could not read" in out.getvalue()


def test_a_run_from_the_terminal_leaves_the_page_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[Path] = []
    monkeypatch.setattr("jaynes.review.ensure_page", opened.append)
    run = started(tmp_path, "tests.stopped:ROLE", "quick=tests.stopped:Agent", "--here")
    cli.main(["run", run, "--home", str(tmp_path)])  # continued: still from the terminal
    assert opened == []
    [w] = Conductor(tmp_path).queue()
    assert w.here  # so the page does not open for it either
    started(tmp_path / "other", "tests.stopped:ROLE", "quick=tests.stopped:Agent")
    assert opened == [tmp_path / "other", tmp_path / "other"]


def test_ask_refuses_while_the_hook_is_missing(tmp_path: Path) -> None:
    run = started(tmp_path, "tests.stopped:ROLE", "quick=tests.stopped:Agent", "--here")
    with pytest.raises(SystemExit, match="hook that records answers"):
        cli.main(["ask", run, "--home", str(tmp_path)])


def test_the_hook_goes_with_the_skill_and_other_hooks_stay() -> None:
    mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-check"}]}
    skill.settings().write_text(json.dumps({"hooks": {"PostToolUse": [mine]}, "model": "opus"}))
    skill.install()
    skill.install()
    data = json.loads(skill.settings().read_text())
    assert data["model"] == "opus" and data["hooks"]["PostToolUse"][0] == mine
    [ours] = data["hooks"]["PostToolUse"][1:]
    assert ours["matcher"] == "AskUserQuestion" and ours["hooks"][0]["command"].endswith(" answered")
    assert skill.hooked()
    skill.remove()
    assert json.loads(skill.settings().read_text()) == {"hooks": {"PostToolUse": [mine]}, "model": "opus"}
    assert not skill.installed() and not skill.hooked()


ROOT = Path(__file__).parents[1]


def test_the_plugin_brings_the_same_skill_and_runs_jaynes_answered_after_the_dialog(tmp_path: Path) -> None:
    assert (ROOT / "skills" / "jaynes" / "SKILL.md").read_text() == skill.SOURCE.read_text()
    [group] = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PostToolUse"]
    assert group["matcher"] == "AskUserQuestion" and "hooks/answered.sh" in group["hooks"][0]["command"]
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "jaynes").write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/args; cat > {tmp_path}/stdin\n')
    (bin_ / "jaynes").chmod(0o755)
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(tmp_path)}
    subprocess.run(["sh", str(ROOT / "hooks" / "answered.sh")], input=b'{"tool_name":"AskUserQuestion"}', env=env,
                   check=True)
    assert (tmp_path / "args").read_text() == "answered\n"
    assert (tmp_path / "stdin").read_text() == '{"tool_name":"AskUserQuestion"}'
    done = subprocess.run(["sh", str(ROOT / "hooks" / "answered.sh")], input=b"{}", capture_output=True,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "nobody")})
    assert (done.returncode, done.stdout) == (0, b"")  # no jaynes: nothing to record, and nothing breaks


def test_with_the_plugin_jaynes_writes_no_skill_or_hook_and_takes_away_earlier_ones(
        capsys: pytest.CaptureFixture[str]) -> None:
    skill.install()  # an earlier jaynes skill
    skill.plugins().write_text(json.dumps({"version": 2, "plugins": {"jaynes@jaynes": [{"scope": "user"}]}}))
    assert skill.plugin() and skill.hooked()
    cli.main(["skill"])
    assert "plugin is installed" in capsys.readouterr().out
    assert not skill.installed() and "answered" not in skill.settings().read_text()
    assert skill.hooked()  # the plugin's hook
    skill.settings().write_text(json.dumps({"enabledPlugins": {"jaynes@jaynes": False}}))
    assert not skill.plugin() and not skill.hooked()
