"""Role whose stop says how it shows its output: a sentence beside a picture an earlier step left, and a cost folded
below; for ``jaynes run``."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from jaynes.role import Picture, Shown, StepContext, Witnesses, role, stop
from jaynes.runtime import TurnResult
from tests import fakes

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x05\xa0\x00\x00\x03\x84"  # a header: 1440 by 900


class Note(BaseModel):
    text: str


def capture(ctx: StepContext) -> Note:
    (ctx.workspace / "files").mkdir()
    (ctx.workspace / "files" / "screen.png").write_bytes(PNG)
    return Note(text="one capture")


def shows(out: Note, needs: Witnesses) -> list[Shown]:
    return [Shown("The list hides the status", f"{out.text}: the status goes beside the name.",
                  (Picture(LOOK.name, "screen.png", "before"),)),
            Shown("Cost", "12 new tokens.", more=True)]


ROLE = role("shown", agent="quick")
LOOK = ROLE.step("look", output=Note, run_by=capture)
ROLE.step("change", output=Note, needs=[LOOK], stop=stop(0.2, sampled=False), shows=shows,
          question=lambda out, _: "Make this change?")


class Agent:
    name = "quick"

    def turn(self, workspace: Path, scratch: Path, prompt: str, deadline: float, web: bool = False) -> TurnResult:
        fakes.write({"text": "Say it"})(workspace)
        return TurnResult(10, 1, "quick-1")
