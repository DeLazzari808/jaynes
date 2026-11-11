"""Role whose first step draws a page an agent wrote, and whose stop shows the picture with a link to the page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from jaynes.role import Picture, Shown, StepContext, role, stop
from jaynes.runtime import TurnResult
from tests import fakes

PNG = b"\x89PNG\r\n\x1a\nnot really"
PAGE = "<html><body><h1>Ledger</h1><script>fetch('/api/home')</script><img src='http://127.0.0.1/x.png'></body></html>"


class Note(BaseModel):
    text: str


def draw(ctx: StepContext) -> Note:
    (ctx.workspace / "files").mkdir()
    (ctx.workspace / "files" / "direction-1.png").write_bytes(PNG)
    (ctx.workspace / "files" / "direction-1.html").write_text(PAGE)
    return Note(text="drawn")


def shows(out: Note, needs: Any) -> list[Shown]:
    return [Shown("Ledger", "warm paper", (Picture("draw", "direction-1.png", "Ledger", page="direction-1.html"),)),
            Shown("Board", "dark", (Picture("draw", "direction-1.png", "Board", page="missing.html"),))]


ROLE = role("paged", agent="quick")
DRAW = ROLE.step("draw", output=Note, run_by=draw)
ROLE.step("pick", output=Note, needs=[DRAW], stop=stop(0.2), shows=shows)


class Agent:
    name = "quick"

    def turn(self, workspace: Path, scratch: Path, prompt: str, deadline: float) -> TurnResult:
        fakes.write({"text": "chosen"})(workspace)
        return TurnResult(10, 1, "quick-1")
