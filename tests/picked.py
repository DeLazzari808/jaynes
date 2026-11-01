"""A role whose stop lets the person choose among the output's items, in order. Loaded by ``jaynes run``."""

from __future__ import annotations

from pathlib import Path

from jaynes.runtime import TurnResult
from tests import fakes
from tests.test_choose_back import FOUR, PICK

ROLE = PICK


class Agent:
    name = "fake"

    def turn(self, workspace: Path, scratch: Path, prompt: str, deadline: float) -> TurnResult:
        fakes.write(FOUR)(workspace)
        return TurnResult(10, 1, "fake-1")
