"""Which Jaynes is installed, whether a newer one is out, and the update, which is the same install again.

An install from git records its address and commit in ``direct_url.json`` (PEP 610), so the commit is the
version: ``git ls-remote`` says what the address points at now. A clone or a package index records no commit,
and then there is nothing to compare or update.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Installed:
    url: str
    commit: str

    @property
    def short(self) -> str:
        return self.commit[:7]


def installed() -> Installed | None:
    """Read from disk each time, so a process that started before an update can see it happened."""
    try:
        info: dict[str, Any] = json.loads(distribution("jaynes-ai").read_text("direct_url.json") or "{}")
    except (PackageNotFoundError, ValueError):
        return None
    vcs: dict[str, Any] = info.get("vcs_info") or {}
    if vcs.get("vcs") != "git" or not vcs.get("commit_id") or not info.get("url"):
        return None
    return Installed(str(info["url"]), str(vcs["commit_id"]))


RUNNING = installed()  # what this process imported


def latest(url: str, timeout: float = 15) -> str | None:
    """The commit ``url`` points at now, or None when it cannot be reached."""
    try:
        done = subprocess.run(["git", "ls-remote", url, "HEAD"], capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})  # a private address fails, not asks
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = done.stdout.split()
    return words[0] if done.returncode == 0 and words else None


def uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    for place in (Path.home() / ".local" / "bin" / "uv", Path.home() / ".cargo" / "bin" / "uv",
                  Path("/opt/homebrew/bin/uv")):
        if place.is_file():
            return str(place)
    return None


def reinstall(at: Installed) -> tuple[bool, str]:
    """Install Jaynes again from the address it came from, as the setup did. Returns success and uv's output."""
    tool = uv()
    if tool is None:
        return False, "uv is not installed; install it, then run: uv tool install --reinstall git+" + at.url
    try:
        done = subprocess.run([tool, "tool", "install", "--reinstall", f"git+{at.url}"], capture_output=True,
                              text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, "uv took more than ten minutes"
    return done.returncode == 0, (done.stdout + done.stderr).strip()
