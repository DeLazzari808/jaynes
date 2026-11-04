"""What a real agent's box lets it do: on a project step, run the project's own tests offline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jaynes.preview import link_dependencies
from jaynes.runtime import QuotaExhausted, Ran, RuntimeFailure
from jaynes.runtimes import ClaudeRuntime, _project_env, _run, claude_commands, claude_failure, claude_settings, codex_command  # pyright: ignore[reportPrivateUsage]
from jaynes.suite import environment


def project_step(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """A turn's workspace whose project copy has a virtualenv linked from the person's folder."""
    python = tmp_path / "pythons" / "cpython-3.12"
    (python / "bin").mkdir(parents=True)
    source = tmp_path / "person"
    (source / ".venv" / "bin").mkdir(parents=True)
    (source / ".venv" / "pyvenv.cfg").write_text(f"home = {python / 'bin'}\nversion_info = 3.12\n")
    ws, scratch = tmp_path / "turn" / "workspace", tmp_path / "turn" / "scratch"
    (ws / "project").mkdir(parents=True)
    scratch.mkdir()
    link_dependencies(source, ws / "project")
    return ws, scratch, source, python


def test_on_a_project_step_claude_may_read_the_dependencies_write_caches_and_use_local_ports(
        tmp_path: Path) -> None:
    ws, scratch, source, python = project_step(tmp_path)
    box = claude_settings(ws, scratch=scratch)["sandbox"]
    assert box["filesystem"]["allowWrite"] == [str(scratch)]
    assert str(scratch) in box["filesystem"]["allowRead"]  # caches written there are read back: Go's, under ~/.jaynes
    assert {str(ws), str((source / ".venv").resolve()), str(python.resolve())} <= set(box["filesystem"]["allowRead"])
    assert box["network"]["allowLocalBinding"] is True and box["network"]["allowedDomains"] == []


def test_claude_may_read_the_python_folder_through_the_link_the_virtualenv_names(tmp_path: Path) -> None:
    # uv's venvs name cpython-3.12, a link to cpython-3.12.13; the box refuses to run a path through a link it cannot read
    real = tmp_path / "pythons" / "cpython-3.12.13"
    (real / "bin").mkdir(parents=True)
    alias = tmp_path / "pythons" / "cpython-3.12"
    alias.symlink_to(real, target_is_directory=True)
    source = tmp_path / "person"
    (source / ".venv" / "bin").mkdir(parents=True)
    (source / ".venv" / "pyvenv.cfg").write_text(f"home = {alias / 'bin'}\nversion_info = 3.12\n")
    ws = tmp_path / "turn" / "workspace"
    (ws / "project").mkdir(parents=True)
    link_dependencies(source, ws / "project")
    reads = set(claude_settings(ws)["sandbox"]["filesystem"]["allowRead"])
    assert {str(alias), str(real.resolve())} <= reads


def test_claude_may_read_the_toolchain_that_runs_the_projects_tests_even_when_it_is_installed_at_home(
        tmp_path: Path, monkeypatch: Any) -> None:
    # Go installed by mise lives under ~/.local, which the box hides: without it the agent cannot run go test
    go = tmp_path / "home" / ".local" / "share" / "mise" / "installs" / "go" / "1.27.1"
    (go / "bin").mkdir(parents=True)
    (go / "bin" / "go").write_text("#!/bin/sh\n")
    (go / "bin" / "go").chmod(0o755)
    monkeypatch.setenv("PATH", f"{go / 'bin'}:/usr/bin:/bin")
    ws, scratch, _, _ = project_step(tmp_path)
    (ws / "inputs").mkdir()
    (ws / "inputs" / "runner.json").write_text(json.dumps({"kind": "go", "suite": "go test ./...", "each": "go test",
                                                           "found": "go.mod"}))
    assert str(go.resolve()) in claude_settings(ws, scratch=scratch)["sandbox"]["filesystem"]["allowRead"]


def test_a_step_without_a_project_gets_no_local_ports_and_reads_only_its_workspace_and_scratch(tmp_path: Path) -> None:
    box = claude_settings(tmp_path, scratch=tmp_path / "scratch")["sandbox"]
    assert box["filesystem"]["allowRead"] == [str(tmp_path), str(tmp_path / "scratch")]
    assert "allowLocalBinding" not in box["network"]


def test_project_commands_keep_their_caches_in_scratch_and_find_the_copys_virtualenv(tmp_path: Path) -> None:
    ws, scratch, _, _ = project_step(tmp_path)
    env = environment(ws / "project", scratch)
    assert env["UV_CACHE_DIR"].startswith(str(scratch)) and env["XDG_CACHE_HOME"].startswith(str(scratch))
    assert env["PATH"].startswith(str(ws / "project" / ".venv" / "bin"))
    assert env["UV_OFFLINE"] == "1" and env["UV_NO_SYNC"] == "1"


def test_an_agent_on_a_project_step_starts_with_the_projects_environment(tmp_path: Path) -> None:
    ws, scratch, _, _ = project_step(tmp_path)
    code, out, _ = _run(["sh", "-c", "echo $TMPDIR $UV_OFFLINE"], ws, scratch, "", 10, _project_env(ws, scratch))
    assert (code, out.split()) == (0, [str(scratch), "1"])


def test_the_commands_a_claude_turn_ran_come_from_its_stream_with_how_each_ended() -> None:
    def use(i: str, name: str, **args: str) -> dict[str, object]:
        return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "name": name, "input": args}]}}

    def result(i: str, content: object, error: bool = False) -> dict[str, object]:
        return {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": i, "content": content, "is_error": error}]}}

    lines = [use("1", "Bash", command="uv run pytest -q", description="tests"), result("1", "Exit code 1\n1 failed", True),
             use("2", "Read", file_path="x"), result("2", "contents"),
             use("3", "Bash", command="uv run pytest -q"), result("3", [{"type": "text", "text": "a" * 5000 + "3 passed"}]),
             use("4", "Bash", command="sleep 999")]
    ran = claude_commands("\n".join(json.dumps(x) for x in lines))
    assert ran[0] == Ran("uv run pytest -q", False, "Exit code 1\n1 failed")
    assert ran[1].ok and ran[1].output.endswith("3 passed") and len(ran[1].output) <= 2000
    assert ran[2] == Ran("sleep 999", False, "")  # the turn ended before it did
    assert len(ran) == 3


def test_an_event_whose_message_is_text_is_not_a_command() -> None:
    lines = [{"type": "system", "message": "text, not a message object"},
             {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "1", "name": "Bash",
                                                            "input": {"command": "true"}}]}}]
    assert claude_commands("\n".join(json.dumps(x) for x in lines)) == [Ran("true", False, "")]


def test_a_codex_command_item_says_what_ran_and_how_it_ended() -> None:
    item = {"id": "i1", "type": "command_execution", "command": "bash -lc 'pytest -q'",
            "aggregated_output": "1 failed\n", "exit_code": 1, "status": "failed"}
    assert codex_command(item) == Ran("bash -lc 'pytest -q'", False, "1 failed\n")
    assert codex_command({**item, "exit_code": 0, "status": "completed"}) == Ran("bash -lc 'pytest -q'", True, "1 failed\n")
    assert codex_command({"type": "agent_message", "text": "hi"}) is None


def test_a_claude_turn_pauses_for_quota_only_when_claude_says_so_not_when_the_agent_does() -> None:
    reset = claude_failure({"is_error": True, "subtype": "success", "result": "Claude AI usage limit reached|1761000000"},
                           1, "")
    assert isinstance(reset, QuotaExhausted) and reset.until == 1761000000
    assert isinstance(claude_failure({"is_error": True, "result": "x"}, 1, "API Error: 429 rate limit"), QuotaExhausted)
    said = "Fixed the bug: the retry now backs off when the rate limit is reached."  # the agent's last message
    assert isinstance(claude_failure({"is_error": False, "subtype": "success", "result": said}, 1, ""), RuntimeFailure)
    assert isinstance(claude_failure({"is_error": True, "subtype": "error_max_turns", "result": said}, 1, ""),
                      RuntimeFailure)


def test_a_claude_turn_has_200_messages_unless_its_step_says_and_running_out_says_so_plainly(tmp_path: Path) -> None:
    def limit(args: list[str]) -> str:
        return args[args.index("--max-turns") + 1]

    claude = ClaudeRuntime()
    assert limit(claude.command(tmp_path)) == "200" and limit(claude.command(tmp_path, max_messages=500)) == "500"
    out = claude_failure({"is_error": True, "subtype": "error_max_turns"}, 1, "")
    assert isinstance(out, RuntimeFailure) and "ran out of messages" in str(out) and "max_messages" in str(out)


def test_a_turn_says_how_much_of_its_input_was_read_back_from_the_cache(tmp_path: Path, monkeypatch: Any) -> None:
    usage = {"input_tokens": 5, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 9000, "output_tokens": 40}
    stream = json.dumps({"type": "result", "subtype": "success", "is_error": False, "usage": usage,
                         "modelUsage": {"claude-x": {}}})
    monkeypatch.setattr("jaynes.runtimes._run", lambda *a, **k: (0, stream + "\n", ""))  # pyright: ignore[reportUnknownLambdaType]
    monkeypatch.setattr("jaynes.runtimes._binary", lambda name: name)  # pyright: ignore[reportUnknownLambdaType]
    turn = ClaudeRuntime().turn(tmp_path, tmp_path, "p", 10)
    assert (turn.tokens_in, turn.tokens_cached, turn.tokens_out) == (9305, 9000, 40)  # new input: 305
