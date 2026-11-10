"""Two roles a plan chains, loaded by ``jaynes run``: one makes a look on its branch, the next builds on it."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from jaynes.role import role
from jaynes.runtime import TurnResult


class Note(BaseModel):
    text: str


LOOK = role("look", agent="planner")
LOOK.step("paint", output=Note, project="write", may_change=["**"])

SCREEN = role("screen", agent="planner")
SCREEN.step("build", output=Note, project="write", may_change=["**"])

NOTES = role("notes", agent="planner")  # no project: it delivers its outputs alone
NOTES.step("write", output=Note)


class Agent:
    """Paints a look, or builds a screen from whatever look its copy of the project has, and says what it heard."""

    name = "planner"

    def turn(self, workspace: Path, scratch: Path, prompt: str, deadline: float) -> TurnResult:
        step = json.loads((workspace / "step.json").read_text())["step"]
        project = workspace / "project"
        if step == "paint":
            (project / "look.css").write_text("body { color: #102030; }\n")
            text = "dark"
        elif step == "build":
            look = project / "look.css"
            seen = look.read_text().strip() if look.is_file() else "no look"
            (project / "screen.html").write_text(f"<style>{seen}</style>\n")
            text = seen
        else:
            text = "notes"
        heard = [line for line in prompt.splitlines() if line.startswith("- ") and "run r" in line]
        (workspace / "output.json").write_text(json.dumps({"text": " | ".join([text, *heard])}))
        return TurnResult(10, 1, "planner-1")
