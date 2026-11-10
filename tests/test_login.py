"""The person's page starts when they log in to their Mac, quietly, so its address always answers."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from jaynes import login
from tests.test_review import form, get, post, serving

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="a launch agent is macOS's")
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The home Jaynes uses by default, with the page allowed, as the person's own."""
    h = tmp_path / "home"
    h.mkdir()
    (h / "guard.json").write_text('{"on": true, "owners": []}')  # Setup reads it, and never asks gh
    monkeypatch.setenv("JAYNES_HOME", str(h))
    monkeypatch.delenv("JAYNES_NO_PAGE")
    return h


def item() -> dict[str, object]:
    with login.plist().open("rb") as f:
        return plistlib.load(f)


def test_the_persons_page_writes_an_item_that_starts_it_at_login_without_a_tab(home: Path) -> None:
    assert login.install(home, 8740) is True

    data = item()
    args = data["ProgramArguments"]
    assert isinstance(args, list)
    assert args[-6:] == ["review", "--home", str(home.resolve()), "--port", "8740", "--at-login"]
    assert data["Label"] == login.LABEL and data["RunAtLoad"] is True
    assert data["EnvironmentVariables"] == {"PATH": os.environ["PATH"], "JAYNES_HOME": str(home)}
    assert "StandardOutPath" not in data  # the page prints its key there
    assert login.install(home, 8740) is False  # already there: macOS is not told again


def test_only_the_default_home_on_a_page_that_may_start_gets_the_item(home: Path, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    assert login.install(tmp_path / "trial", 8740) is False  # a trial's home never replaces the person's
    monkeypatch.setenv("JAYNES_NO_PAGE", "1")
    assert login.install(home, 8740) is False
    assert not login.plist().exists()


def test_turned_off_it_is_removed_and_stays_away_until_turned_on(home: Path) -> None:
    login.install(home, 8740)

    login.switch(home, False)
    assert not login.plist().exists()
    assert login.install(home, 8740) is False  # the next start of the page leaves it off

    login.switch(home, True)
    assert login.program() is not None


@pytest.fixture
def served(home: Path) -> Iterator[str]:
    base, server = serving(home)
    yield base
    server.shutdown()


def login_part(page: str) -> str:
    start = page.index("The page at login")
    return page[start:page.index("Action gate in Claude Code")]


def test_setup_shows_the_address_and_switches_it(served: str, home: Path) -> None:
    part = login_part(get(served + "/setup"))
    assert "http://127.0.0.1:8740/" in part and "bookmark" in part
    assert 'value="login-on"' in part  # not there yet: this page was not started as the person's

    page = get(served + "/setup")
    assert post(served + "/setup", {**form(page), "action": "login-on"})[0] == 303
    assert login.program() is not None
    assert 'value="login-off"' in login_part(get(served + "/setup"))

    assert post(served + "/setup", {"token": "wrong", "action": "login-off"})[0] == 403
    assert login.program() is not None
    assert post(served + "/setup", {**form(page), "action": "login-off"})[0] == 303
    assert login.program() is None
    assert json.loads((home / "page.json").read_text()) == {"at_login": False}


def test_setup_has_no_switch_for_another_home(home: Path, tmp_path: Path) -> None:
    base, server = serving(tmp_path / "trial")
    try:
        page = get(base + "/setup")
        assert "The page at login" not in page
        assert post(base + "/setup", {**form(page), "action": "login-on"})[0] == 400
    finally:
        server.shutdown()
    assert not login.plist().exists()


def start(home: Path, port: int, how: str, user: Path, opened: Path,
          default: Path | None = None) -> subprocess.Popen[bytes]:
    """``jaynes review`` as macOS or the person starts it, with a browser that only notes what it was asked to open."""
    browser = opened.with_name(f"{opened.name}-browser")
    user.mkdir(parents=True, exist_ok=True)
    browser.write_text(f'#!/bin/sh\necho "$1" >> "{opened}"\n')
    browser.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "JAYNES_NO_PAGE"}
    env.update(PYTHONPATH=str(REPO), HOME=str(user), JAYNES_HOME=str(default or home), BROWSER=str(browser))
    return subprocess.Popen([sys.executable, "-m", "jaynes.cli", "review", "--home", str(home), "--port", str(port),
                             how], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def up(port: int, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    while True:
        try:
            get(f"http://127.0.0.1:{port}/seq")
            return
        except OSError:
            assert server.poll() is None, server.stderr.read().decode() if server.stderr else ""
            assert time.monotonic() < deadline
            time.sleep(0.1)


def test_started_at_login_the_page_opens_no_tab_and_a_second_start_leaves_quietly(tmp_path: Path) -> None:
    port = 24000 + os.getpid() % 2000
    user, home, opened = tmp_path / "user", tmp_path / "user" / ".jaynes", tmp_path / "opened"
    shown = start(tmp_path / "shown", port + 1, "--open", user, tmp_path / "shown-opened", default=home)
    server = start(home, port, "--at-login", user, opened)
    try:
        up(port + 1, shown)
        up(port, server)
        deadline = time.monotonic() + 10
        while not (tmp_path / "shown-opened").exists():  # the person's start opens a tab, after half a second
            assert time.monotonic() < deadline
            time.sleep(0.1)
        time.sleep(1)
        assert not opened.exists()  # the one at login opens none

        with (user / "Library" / "LaunchAgents" / f"{login.LABEL}.plist").open("rb") as f:
            args = plistlib.load(f)["ProgramArguments"]
        assert args[-5:] == ["--home", str(home.resolve()), "--port", str(port), "--at-login"]

        again = start(home, port, "--at-login", user, opened)
        assert again.wait(timeout=15) == 0
        assert again.stderr is not None and again.stderr.read() == b""
    finally:
        server.kill()
        shown.kill()
