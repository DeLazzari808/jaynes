"""The /jaynes command in Claude Code: a skill Jaynes writes to ``~/.claude/skills/jaynes`` and keeps current."""

from __future__ import annotations

from pathlib import Path

SOURCE = Path(__file__).with_name("SKILL.md")


def path() -> Path:
    return Path.home() / ".claude" / "skills" / "jaynes" / "SKILL.md"


def installed() -> bool:
    return path().is_file()


def install() -> Path:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text() != SOURCE.read_text():
        target.write_text(SOURCE.read_text())
    return target


def refresh() -> None:
    """Bring an installed skill to this version's text; one never installed stays absent."""
    if installed():
        install()
