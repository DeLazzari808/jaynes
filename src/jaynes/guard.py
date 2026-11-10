"""The guard's settings, as the Setup page reads and changes them.

The guard itself is hooks/guard.py in the plugin, a standard-library script that reads the same file:
``$JAYNES_HOME/guard.json`` with "on" (true when missing) and "owners", the GitHub owners whose repositories pass
freely. With no valid owners list the person's GitHub user, found with gh, is the one free owner; it is saved
there as "github_user" too, so the page can mark it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from jaynes.policy import jaynes_home

OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
GH_TIMEOUT = 5.0


@dataclass(frozen=True)
class Guard:
    on: bool
    owners: tuple[str, ...]
    github_user: str | None


def path() -> Path:
    return jaynes_home().expanduser() / "guard.json"


def _read() -> dict[str, object]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cast(dict[str, object], data) if isinstance(data, dict) else {}


def _github_user() -> str | None:
    try:
        done = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True,
                              timeout=GH_TIMEOUT, stdin=subprocess.DEVNULL,
                              env=dict(os.environ, GH_PROMPT_DISABLED="1"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    login = done.stdout.strip()
    return login if done.returncode == 0 and OWNER.fullmatch(login) else None


def load() -> Guard:
    """The settings; a missing or broken file means on, with the GitHub user found by gh, saved for next time."""
    data = _read()
    on = data.get("on", True)
    on = on if isinstance(on, bool) else True
    found = data.get("github_user")
    found = found if isinstance(found, str) and OWNER.fullmatch(found) else None
    owners = data.get("owners")
    listed = [o for o in cast(list[object], owners) if isinstance(o, str)] if isinstance(owners, list) else None
    if listed is not None and len(listed) == len(cast(list[object], owners)):
        return Guard(on, tuple(o for o in listed if o.strip()), found)
    login = _github_user()
    guard = Guard(on, (login,) if login else (), login)
    if login:
        save(guard)
    return guard


def save(guard: Guard) -> None:
    data: dict[str, object] = {"on": guard.on, "owners": list(guard.owners)}
    if guard.github_user:
        data["github_user"] = guard.github_user
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def switch(on: bool) -> None:
    g = load()
    save(Guard(on, g.owners, g.github_user))


def add_owner(owner: str) -> None:
    """Adds a GitHub owner; ValueError when it is not a GitHub user or organization name."""
    owner = owner.strip().lstrip("@")
    if not OWNER.fullmatch(owner):
        raise ValueError(f"{owner or 'An empty name'} is not a GitHub user or organization name")
    g = load()
    if owner.lower() not in (o.lower() for o in g.owners):
        save(Guard(g.on, (*g.owners, owner), g.github_user))


def remove_owner(owner: str) -> None:
    g = load()
    save(Guard(g.on, tuple(o for o in g.owners if o.lower() != owner.strip().lower()), g.github_user))
