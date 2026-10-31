"""The /jaynes command in Claude Code: a skill Jaynes writes to ``~/.claude/skills/jaynes`` and keeps current.

With it goes a hook on Claude Code's question dialog (``jaynes answered``): a stop asked in the terminal is
answered there, and Claude Code hands the hook what the person chose, so the answer is the person's and not
something an agent reports.

The Jaynes plugin (``/plugin install jaynes --marketplace alk0x1/jaynes``) brings the same skill and hook, installed
by the person instead of written by a command an agent runs. Where it is installed, Jaynes writes neither, and
takes away the ones an earlier ``jaynes skill`` wrote, so nothing runs twice. The skill says only when to use
Jaynes; its steps are ``guide.md``, printed by ``jaynes guide``, so they change with Jaynes and not with the
plugin, which ``jaynes update`` brings to the same commit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

from jaynes import install as gate
from jaynes.policy import jaynes_home

SOURCE = Path(__file__).with_name("SKILL.md")
GUIDE = Path(__file__).with_name("guide.md")  # the steps, which change with Jaynes and not with the plugin


def path() -> Path:
    return Path.home() / ".claude" / "skills" / "jaynes" / "SKILL.md"


def settings() -> Path:
    return gate.settings_path()


def plugins() -> Path:
    return Path.home() / ".claude" / "plugins" / "installed_plugins.json"


def installed() -> bool:
    return path().is_file()


def plugin() -> bool:
    """Whether the Jaynes plugin is installed and not turned off."""
    return _installed_plugin() is not None


def _installed_plugin() -> tuple[str, str] | None:
    """The Jaynes plugin's name in Claude Code (jaynes@<marketplace>) and its version, when installed and on."""
    try:
        found: dict[str, Any] = json.loads(plugins().read_text()).get("plugins", {})
    except (OSError, ValueError, AttributeError):
        return None
    enabled: dict[str, Any] = gate.read_settings(settings()).get("enabledPlugins", {})
    for name, installs in found.items():
        if name.split("@")[0] == "jaynes" and enabled.get(name) is not False:
            first: Any = cast(list[Any], installs)[0] if isinstance(installs, list) and installs else {}
            return name, str(cast(dict[str, Any], first).get("version", "")) if isinstance(first, dict) else ""
    return None


def update_plugin(commit: str) -> str | None:
    """Bring the plugin to ``commit`` with Claude Code's own commands. Says what happened, or None when there is
    nothing to do. Claude Code names a plugin without a version by its commit, so the two compare."""
    found = _installed_plugin()
    if found is None or (found[1] and commit.startswith(found[1])):
        return None
    name = found[0]
    claude = shutil.which("claude") or next((str(p) for p in (Path.home() / ".local" / "bin" / "claude",
                                                             Path.home() / ".claude" / "local" / "claude")
                                             if p.is_file()), None)
    if claude is None:
        return "the Jaynes plugin is behind; update it in Claude Code with /plugin"
    try:
        for command in (["plugin", "marketplace", "update", name.split("@", 1)[1]], ["plugin", "update", name]):
            done = subprocess.run([claude, *command], capture_output=True, text=True, timeout=180)
            if done.returncode != 0:
                return f"the Jaynes plugin was not updated: {(done.stdout + done.stderr).strip()[-500:]}"
    except (OSError, subprocess.TimeoutExpired) as err:
        return f"the Jaynes plugin was not updated: {err}"
    return "The Jaynes plugin updated too; Claude Code uses it from the next session."


def install() -> Path:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text() != SOURCE.read_text():
        target.write_text(SOURCE.read_text())
    _hook(on=True)
    return target


def remove() -> None:
    path().unlink(missing_ok=True)
    _hook(on=False)


def refresh() -> None:
    """Bring an installed skill to this version's text, and its hook with it; one never installed stays absent.
    Where the plugin brings both, an earlier install goes away."""
    if plugin():
        if installed() or _settings_hook():
            remove()
    elif installed():
        install()


def hook_command() -> str:
    return gate.hook_command(jaynes_home()).removesuffix(" hook") + " answered"


def _ours(command: str) -> bool:
    words = command.split()
    return len(words) >= 2 and words[-1] == "answered" and "jaynes" in command


def hooked() -> bool:
    """Whether Claude Code runs ``jaynes answered`` after its question dialog."""
    return plugin() or _settings_hook()


def _settings_hook() -> bool:
    hooks: dict[str, Any] = gate.read_settings(settings()).get("hooks", {})
    return any(_ours(str(h.get("command", ""))) for g in hooks.get("PostToolUse", []) for h in g.get("hooks", []))


def _hook(on: bool) -> None:
    """Put this ``jaynes answered`` after Claude Code's question dialog, or take it away; other hooks are kept."""
    data = gate.read_settings(settings())
    hooks: dict[str, Any] = data.setdefault("hooks", {})
    groups = [{**g, "hooks": [h for h in g.get("hooks", []) if not _ours(str(h.get("command", "")))]}
              for g in hooks.get("PostToolUse", [])]
    groups = [g for g in groups if g["hooks"]]
    if on:
        groups.append({"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": hook_command()}]})
    if groups:
        hooks["PostToolUse"] = groups
    else:
        hooks.pop("PostToolUse", None)
    if not hooks:
        del data["hooks"]
    gate.write_settings(settings(), data)
