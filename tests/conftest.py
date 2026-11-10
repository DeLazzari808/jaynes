from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))  # tests import roles as software.fix_bug


@pytest.fixture(autouse=True)
def no_page(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Runs in tests open no review page, and nothing writes to the person's ~/.claude or ~/.jaynes."""
    monkeypatch.setenv("JAYNES_NO_PAGE", "1")
    monkeypatch.setenv("JAYNES_HOME", str(tmp_path / "jaynes-home"))
    monkeypatch.setattr("jaynes.skill.path", lambda: tmp_path / "claude-skills" / "jaynes" / "SKILL.md")
    monkeypatch.setattr("jaynes.skill.settings", lambda: tmp_path / "claude-settings.json")
    monkeypatch.setattr("jaynes.skill.plugins", lambda: tmp_path / "claude-plugins.json")
