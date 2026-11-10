"""The person's page starts when they log in to their Mac, so its address always answers.

A launch agent, a file in ~/Library/LaunchAgents that macOS reads at login to start a program by itself, runs the
page for the person's home quietly: it opens no tab, and like any open page it calls the person when something
holds a run and continues the runs whose usage-limit pause has ended. The person's page writes the file when it
starts, unless the person turned it off on the Setup page, which removes the file and keeps the choice in the home's
``page.json``. Only on macOS and for the home Jaynes uses by default, so a test's or a trial's home never replaces
it.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import sys
from pathlib import Path
from typing import cast

from jaynes.policy import jaynes_home

LABEL = "dev.jaynes.page"
PORT = 8740


def agents() -> Path:
    """The folder macOS reads at login."""
    return Path.home() / "Library" / "LaunchAgents"


def plist() -> Path:
    return agents() / f"{LABEL}.plist"


def supported(home: Path) -> bool:
    """macOS, and the home Jaynes uses when none is given."""
    return sys.platform == "darwin" and home.expanduser().resolve() == jaynes_home().expanduser().resolve()


def wanted(home: Path) -> bool:
    """False once the person turned it off on the Setup page."""
    try:
        data = json.loads((home / "page.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    on = cast(dict[str, object], data).get("at_login", True) if isinstance(data, dict) else True
    return on if isinstance(on, bool) else True


def port(home: Path) -> int:
    """The port ``home``'s page runs on, or the one it starts on."""
    try:
        found = json.loads((home / "review.json").read_text(encoding="utf-8")).get("port")
    except (OSError, ValueError, AttributeError):
        return PORT
    return found if isinstance(found, int) and found > 0 else PORT


def command(home: Path, port: int) -> list[str]:
    """The installed jaynes when there is one, so the item outlives whichever copy of Jaynes wrote it."""
    found = shutil.which("jaynes")
    head = [found] if found else [sys.executable, "-m", "jaynes.cli"]
    return [*head, "review", "--home", str(home.expanduser().resolve()), "--port", str(port), "--at-login"]


def program() -> list[str] | None:
    """What the item starts, if Jaynes's item is there."""
    try:
        with plist().open("rb") as f:
            data = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    if not isinstance(data, dict) or cast(dict[str, object], data).get("Label") != LABEL:
        return None
    args = cast(dict[str, object], data).get("ProgramArguments")
    return [str(a) for a in cast(list[object], args)] if isinstance(args, list) else None


def install(home: Path, port: int = PORT) -> bool:
    """Write the item for ``home``'s page on ``port``, unless it is there already or the person turned it off; True
    when it wrote. It runs with this process's PATH, so the runs it continues find claude, git and gh."""
    if os.environ.get("JAYNES_NO_PAGE") or not supported(home) or not wanted(home):
        return False
    args = command(home, port)
    if program() == args:
        return False
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if os.environ.get("JAYNES_HOME"):
        env["JAYNES_HOME"] = os.environ["JAYNES_HOME"]
    data = {"Label": LABEL, "ProgramArguments": args, "RunAtLoad": True, "EnvironmentVariables": env,
            "ProcessType": "Interactive",  # the agents of the runs it continues are not slowed as background work
            "StandardErrorPath": str(home.expanduser().resolve() / "review.log")}  # never stdout: it has the key
    target = plist()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    tmp.write_bytes(plistlib.dumps(data))
    os.replace(tmp, target)
    return True


def switch(home: Path, on: bool) -> None:
    """The Setup page's switch: keep the choice, and add or remove the item. Removing it leaves the page running."""
    choice = home / "page.json"
    choice.parent.mkdir(parents=True, exist_ok=True)
    choice.write_text(json.dumps({"at_login": on}) + "\n", encoding="utf-8")
    if on:
        install(home, port(home))
    elif program() is not None:
        plist().unlink(missing_ok=True)
