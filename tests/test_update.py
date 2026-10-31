"""Jaynes knows the commit it was installed from, updates by installing again, and installs /jaynes in Claude Code."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from jaynes import cli, skill, update
from jaynes.update import Installed

AT = Installed("https://github.com/alk0x1/jaynes", "832f495a034fc3981fcd5fd9220177c9ba23d1c0")


def returns[T](value: T) -> Callable[..., T]:
    def f(*args: object, **kwargs: object) -> T:
        return value
    return f


def never(*args: object, **kwargs: object) -> NoReturn:
    pytest.fail("reinstalled when it should not have")


def test_the_commit_comes_from_what_uv_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    class Dist:
        def read_text(self, name: str) -> str | None:
            assert name == "direct_url.json"
            return '{"url":"https://github.com/alk0x1/jaynes","vcs_info":{"vcs":"git","commit_id":"832f495a"}}'

    monkeypatch.setattr(update, "distribution", returns(Dist()))
    assert update.installed() == Installed("https://github.com/alk0x1/jaynes", "832f495a")


def test_a_clone_has_no_commit_to_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    class Dist:
        def read_text(self, name: str) -> str | None:
            return '{"url":"file:///Users/x/jaynes","dir_info":{"editable":true}}'

    monkeypatch.setattr(update, "distribution", returns(Dist()))
    assert update.installed() is None


def test_latest_reads_what_the_address_points_at(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "one"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert update.latest(f"file://{repo}") == head
    assert update.latest(f"file://{tmp_path / 'missing'}") is None


def test_update_does_nothing_when_jaynes_is_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(update, "installed", returns(AT))
    monkeypatch.setattr(update, "latest", returns(AT.commit))
    monkeypatch.setattr(update, "reinstall", never)
    cli.main(["update", "--home", str(tmp_path)])
    assert "up to date (832f495)" in capsys.readouterr().out


def test_update_installs_again_when_the_address_moved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    after = Installed(AT.url, "a6c5dc0" + "0" * 33)
    seen: list[Installed] = []

    def reinstall(at: Installed) -> tuple[bool, str]:
        seen.append(at)
        return True, ""

    def installed() -> Installed:
        return after if seen else AT

    monkeypatch.setattr(update, "installed", installed)
    monkeypatch.setattr(update, "latest", returns(after.commit))
    monkeypatch.setattr(update, "reinstall", reinstall)
    cli.main(["update", "--home", str(tmp_path)])
    assert seen == [AT]
    assert "updated: 832f495 -> a6c5dc0" in capsys.readouterr().out


def test_update_waits_for_runs_that_are_working(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    from jaynes.conductor import Conductor, Waiting
    monkeypatch.setattr(update, "installed", returns(AT))
    monkeypatch.setattr(update, "latest", returns("f" * 40))
    monkeypatch.setattr(update, "reinstall", never)
    monkeypatch.setattr(Conductor, "queue", returns([Waiting("r1", "running", "a", 0, "x", "t")]))
    cli.main(["update", "--home", str(tmp_path)])
    assert "1 run is working" in capsys.readouterr().out


def test_update_from_a_clone_says_where_to_update(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(update, "installed", returns(None))
    cli.main(["update", "--home", str(tmp_path)])
    assert "not installed from git" in capsys.readouterr().out


def test_jaynes_skill_installs_the_command_and_prints_what_to_do(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["skill"])
    text = skill.path().read_text()
    assert text.startswith("---\nname: jaynes\n") and "$ARGUMENTS" in text
    out = capsys.readouterr().out
    assert "/jaynes <task>" in out and "jaynes run --here --role" in out and "jaynes ask" in out


def test_the_skill_only_says_when_and_jaynes_guide_says_how_naming_every_built_in_role(
        capsys: pytest.CaptureFixture[str]) -> None:
    from jaynes.roles import BUILT_IN
    text = skill.SOURCE.read_text()
    assert "jaynes guide" in text and "jaynes run" not in text  # steps change with the CLI, not with the plugin
    cli.main(["guide"])
    out = capsys.readouterr().out
    assert "jaynes run --here --role" in out and "jaynes ask" in out and "$ARGUMENTS" not in out
    assert all(f"`{name}`" in out for name in BUILT_IN)


def test_an_installed_skill_is_kept_current_and_an_absent_one_stays_absent() -> None:
    skill.refresh()
    assert not skill.path().exists()
    skill.install()
    skill.path().write_text("old")
    skill.refresh()
    assert skill.path().read_text() == skill.SOURCE.read_text()


def test_update_brings_the_plugin_to_the_same_commit_through_claude_code(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "claude").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/claude.log\n')
    (bin_ / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    monkeypatch.setattr(update, "installed", returns(AT))
    monkeypatch.setattr(update, "latest", returns(AT.commit))
    skill.plugins().write_text('{"version": 2, "plugins": {"jaynes@jaynes": [{"scope": "user", '
                               '"version": "cb9f7e00212a"}]}}')
    cli.main(["update", "--home", str(tmp_path)])
    assert (tmp_path / "claude.log").read_text() == "plugin marketplace update jaynes\nplugin update jaynes@jaynes\n"
    assert "plugin updated" in capsys.readouterr().out

    (tmp_path / "claude.log").unlink()
    skill.plugins().write_text('{"version": 2, "plugins": {"jaynes@jaynes": [{"scope": "user", '
                               f'"version": "{AT.commit[:12]}"}}]}}}}')
    cli.main(["update", "--home", str(tmp_path)])
    assert not (tmp_path / "claude.log").exists()  # already at that commit
