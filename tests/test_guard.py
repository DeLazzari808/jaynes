"""The plugin's guard end to end: the command in hooks/hooks.json, fed hook JSON on stdin as Claude Code feeds it.

It runs without jaynes installed, so it is run as a plain script, never imported. The GitHub owners are invented."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]


class Box:
    """A machine for the guard: a Jaynes home, a fake gh, and repositories with their origins."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.home = tmp / "jaynes-home"
        self.bin = tmp / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(f'#!/bin/sh\necho "$@" >> {tmp}/gh-calls\n'
                      f'if [ -f {tmp}/gh-sleep ]; then exec sleep 30; fi\n'
                      f'if [ "$*" = "api user --jq .login" ] && [ -f {tmp}/gh-login ]; then cat {tmp}/gh-login; exit 0; fi\n'
                      'echo "You are not logged into any GitHub hosts." >&2\nexit 1\n')
        gh.chmod(0o755)
        self.env = {"PATH": f"{self.bin}:{Path(sys.executable).parent}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    "HOME": str(tmp / "userhome"), "JAYNES_HOME": str(self.home), "CLAUDE_PLUGIN_ROOT": str(ROOT),
                    "GIT_CONFIG_NOSYSTEM": "1"}

    def logged_in(self, login: str) -> None:
        (self.tmp / "gh-login").write_text(login + "\n")

    def gh_calls(self) -> list[str]:
        calls = self.tmp / "gh-calls"
        return calls.read_text().splitlines() if calls.is_file() else []

    def repo(self, name: str, origin: str | None) -> Path:
        path = self.tmp / name
        path.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=path, env=self.env, check=True)
        if origin:
            subprocess.run(["git", "remote", "add", "origin", origin], cwd=path, env=self.env, check=True)
        return path

    def settings(self, text: str) -> None:
        self.home.mkdir(exist_ok=True)
        (self.home / "guard.json").write_text(text)

    def hook(self, cwd: Path, tool: str, tool_input: dict[str, object]) -> dict[str, object] | None:
        """Runs every PreToolUse command the plugin matches to ``tool``; the guard's answer, or None when silent."""
        commands = [h["command"] for group in HOOKS["PreToolUse"] if re.fullmatch(group["matcher"], tool)
                    for h in group["hooks"]]
        assert commands, f"no PreToolUse hook for {tool}"
        event = {"hook_event_name": "PreToolUse", "session_id": "s1", "transcript_path": str(self.tmp / "t.jsonl"),
                 "cwd": str(cwd), "permission_mode": "default", "tool_name": tool, "tool_input": tool_input,
                 "tool_use_id": "toolu_1"}
        said = []
        for command in commands:
            done = subprocess.run(command, shell=True, cwd=cwd, input=json.dumps(event), capture_output=True,
                                  text=True, env=self.env, timeout=60)
            assert done.returncode == 0, (command, done.returncode, done.stderr)  # never 2: it asks, it never blocks
            if done.stdout.strip():
                said.append(json.loads(done.stdout))
        assert len(said) <= 1, said
        return said[0] if said else None

    def bash(self, cwd: Path, command: str) -> dict[str, object] | None:
        return self.hook(cwd, "Bash", {"command": command, "description": "d"})


def asks(out: dict[str, object] | None) -> str:
    """The reason the guard asks with, checking the answer is a PreToolUse ask."""
    assert out is not None, "the guard stayed silent"
    spec = out["hookSpecificOutput"]
    assert isinstance(spec, dict)
    assert spec["hookEventName"] == "PreToolUse" and spec["permissionDecision"] == "ask", out
    reason = spec["permissionDecisionReason"]
    assert isinstance(reason, str) and reason.strip()
    return reason


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


@pytest.fixture
def repos(box: Box) -> tuple[Path, Path]:
    """nora-builds is the person's own GitHub user; quill-labs is someone else."""
    box.logged_in("nora-builds")
    mine = box.repo("notes", "https://github.com/nora-builds/notes.git")
    theirs = box.repo("site", "git@github.com:quill-labs/site.git")
    return mine, theirs


def test_the_plugin_runs_a_standard_library_guard_before_every_shell_command_and_edit() -> None:
    assert [g["matcher"] for g in HOOKS["PostToolUse"]] == ["AskUserQuestion"]  # the answered hook stays
    for tool in ("Bash", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        commands = [h["command"] for g in HOOKS["PreToolUse"] if re.fullmatch(g["matcher"], tool) for h in g["hooks"]]
        assert any("${CLAUDE_PLUGIN_ROOT}/hooks/" in c for c in commands), tool
    for tool in ("Read", "Grep", "AskUserQuestion"):
        assert not [g for g in HOOKS["PreToolUse"] if re.fullmatch(g["matcher"], tool)], tool
    scripts = {m for g in HOOKS["PreToolUse"] for h in g["hooks"]
               for m in re.findall(r"hooks/([\w.-]+\.py)", h["command"])}
    assert scripts
    for script in scripts:
        tree = ast.parse((ROOT / "hooks" / script).read_text())
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)


HELD = ["git push origin main", "git push", "git reset --hard HEAD~1", "git clean -fd", "git branch -D old",
        "git branch --delete --force old", "git tag -d v1.0", "gh pr create --fill", "gh pr merge 12 --squash",
        "gh pr close 12", "gh repo create quill-labs/new --public", "gh repo delete quill-labs/site --yes",
        "gh repo archive quill-labs/site --yes", "ls && git push", "make test; git push", "false || git push",
        "true | git push", "(git push origin main)", "git -c color.ui=never push", "git --no-pager reset --hard"]


@pytest.mark.parametrize("command", HELD)
def test_what_cannot_be_undone_asks_in_someone_elses_repository(box: Box, repos: tuple[Path, Path], command: str) -> None:
    _, theirs = repos
    asks(box.bash(theirs, command))


def test_the_question_says_in_plain_words_what_cannot_be_undone_and_why(box: Box, repos: tuple[Path, Path]) -> None:
    _, theirs = repos
    reason = asks(box.bash(theirs, "git push origin main"))
    assert "git push" in reason and "github.com/quill-labs/site" in reason and "owner" in reason


PASSING = ["git status", "git merge feature", "git branch -d old", "git remote add upstream https://github.com/quill-labs/x",
           "git log --oneline", "git commit -m 'before git push'", 'echo "git push"', "echo 'gh pr merge 12'",
           "cat > notes.md <<'EOF'\ngit push origin main\ngit reset --hard\nEOF", "grep -n 'git clean' README.md",
           "gh pr view 12", "gh pr list", "ls -la", "git pushed-notes", "python -m pytest -q"]


@pytest.mark.parametrize("command", PASSING)
def test_everything_else_passes_untouched(box: Box, repos: tuple[Path, Path], command: str) -> None:
    _, theirs = repos
    assert box.bash(theirs, command) is None


def test_other_tools_and_bad_input_pass_untouched(box: Box, repos: tuple[Path, Path]) -> None:
    _, theirs = repos
    assert box.hook(theirs, "Edit", {"file_path": str(theirs / "a.py"), "old_string": "a", "new_string": "b"}) is None
    assert box.hook(theirs, "Write", {"file_path": str(theirs / "b.md"), "content": "git push"}) is None
    [command] = [h["command"] for g in HOOKS["PreToolUse"] if re.fullmatch(g["matcher"], "Bash") for h in g["hooks"]]
    for stdin in ("", "not json", "{}", '{"tool_name": "Bash", "tool_input": {}}'):
        done = subprocess.run(command, shell=True, cwd=theirs, input=stdin, capture_output=True, text=True,
                              env=box.env, timeout=60)
        assert (done.returncode, done.stdout) == (0, ""), (stdin, done.stderr)


def test_the_persons_own_repositories_pass_freely(box: Box, repos: tuple[Path, Path]) -> None:
    mine, _ = repos
    for command in ("git push origin main", "git reset --hard HEAD~1", "gh pr merge 12", "gh pr create --fill"):
        assert box.bash(mine, command) is None, command
    elsewhere = box.repo("mirror", "https://gitlab.com/nora-builds/notes.git")  # the owner, but not on GitHub
    asks(box.bash(elsewhere, "git push"))
    asks(box.bash(box.repo("loose", None), "git push"))


def test_the_owner_is_found_once_with_gh_and_saved(box: Box, repos: tuple[Path, Path]) -> None:
    mine, theirs = repos
    assert box.bash(mine, "git push") is None
    assert box.gh_calls() == ["api user --jq .login"]
    assert json.loads((box.home / "guard.json").read_text())["owners"] == ["nora-builds"]
    assert box.bash(mine, "git push") is None
    asks(box.bash(theirs, "git push"))
    assert box.gh_calls() == ["api user --jq .login"]  # later commands never wait on the network


def test_without_gh_logged_in_every_hold_asks(box: Box) -> None:
    mine = box.repo("notes", "https://github.com/nora-builds/notes.git")
    asks(box.bash(mine, "git push"))
    assert box.bash(mine, "git status") is None


def test_a_slow_gh_never_holds_the_session(box: Box, repos: tuple[Path, Path]) -> None:
    mine, _ = repos
    (box.tmp / "gh-sleep").write_text("")
    began = time.monotonic()
    asks(box.bash(mine, "git push"))
    assert time.monotonic() - began < 15


def test_gh_names_its_repository_with_R_or_repo(box: Box, repos: tuple[Path, Path]) -> None:
    mine, theirs = repos
    assert box.bash(theirs, "gh pr merge 12 -R nora-builds/notes") is None
    assert box.bash(theirs, "gh pr close 12 --repo nora-builds/notes") is None
    assert box.bash(theirs, "gh pr merge 12 --repo=nora-builds/notes") is None
    asks(box.bash(mine, "gh pr merge 12 -R quill-labs/site"))
    asks(box.bash(mine, "gh pr create --fill --repo quill-labs/site"))


def test_cd_and_git_C_change_the_target_repository(box: Box, repos: tuple[Path, Path]) -> None:
    mine, theirs = repos
    assert box.bash(theirs, f"cd {mine} && git push origin main") is None
    asks(box.bash(mine, f"cd {theirs} && git push origin main"))
    assert box.bash(theirs, f"git -C {mine} push") is None
    asks(box.bash(mine, f"git -C {theirs} push"))
    asks(box.bash(mine, f"git -C {theirs} reset --hard"))


def test_a_configured_owners_list_replaces_the_gh_user(box: Box, repos: tuple[Path, Path]) -> None:
    mine, theirs = repos
    box.settings(json.dumps({"on": True, "owners": ["quill-labs", "otter-co"]}))
    assert box.bash(theirs, "git push") is None
    asks(box.bash(mine, "git push"))
    assert box.gh_calls() == []


def test_the_guard_turned_off_is_silent(box: Box, repos: tuple[Path, Path]) -> None:
    _, theirs = repos
    box.settings(json.dumps({"on": False, "owners": []}))
    for command in ("git push origin main", "git reset --hard", "gh repo delete quill-labs/site --yes"):
        assert box.bash(theirs, command) is None, command


@pytest.mark.parametrize("text", ["", "{not json", "[]", '{"on": "yes", "owners": "quill-labs"}'])
def test_a_broken_settings_file_means_on_with_the_gh_user(box: Box, repos: tuple[Path, Path], text: str) -> None:
    mine, theirs = repos
    box.settings(text)
    assert box.bash(mine, "git push") is None
    asks(box.bash(theirs, "git push"))


def test_a_missing_settings_file_means_on(box: Box, repos: tuple[Path, Path]) -> None:
    _, theirs = repos
    assert not (box.home / "guard.json").exists()
    asks(box.bash(theirs, "git push"))


def test_edits_of_its_own_files_ask(box: Box, repos: tuple[Path, Path]) -> None:
    mine, _ = repos
    box.settings(json.dumps({"on": True, "owners": ["nora-builds"]}))
    own = ROOT / "hooks" / "hooks.json"
    settings = box.home / "guard.json"
    asks(box.hook(mine, "Edit", {"file_path": str(own), "old_string": "ask", "new_string": "allow"}))
    asks(box.hook(mine, "Write", {"file_path": str(settings), "content": '{"on": false}'}))
    asks(box.hook(mine, "MultiEdit", {"file_path": str(ROOT / "hooks" / "answered.sh"), "edits": []}))
    asks(box.hook(mine, "NotebookEdit", {"notebook_path": str(ROOT / "hooks" / "x.ipynb"), "new_source": ""}))
    for command in (f"rm {own}", f"rm -rf {ROOT / 'hooks'}", f"mv /dev/null {own}", f"cp other.json {settings}",
                    f"sed -i '' 's/ask/allow/' {own}", f"echo '{{\"on\": false}}' > {settings}",
                    f"cd {mine} && printf x >> {own}"):
        asks(box.bash(mine, command))
    assert box.bash(mine, f"cat {settings}") is None
    assert box.bash(mine, f"cp {own} {mine / 'copy.json'}") is None
