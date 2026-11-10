from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def no_page(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Runs in tests open no review page, and nothing writes to the person's ~/.claude or login items."""
    monkeypatch.setenv("JAYNES_NO_PAGE", "1")
    monkeypatch.setattr("jaynes.skill.path", lambda: tmp_path / "claude-skills" / "jaynes" / "SKILL.md")
    monkeypatch.setattr("jaynes.skill.settings", lambda: tmp_path / "claude-settings.json")
    monkeypatch.setattr("jaynes.skill.plugins", lambda: tmp_path / "claude-plugins.json")
    monkeypatch.setattr("jaynes.login.agents", lambda: tmp_path / "LaunchAgents")  # never the person's login items
