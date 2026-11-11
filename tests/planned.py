"""Roles a plan chains, loaded by ``jaynes run``: one makes a look on its branch, the next builds on it; and one that
plans them."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from jaynes.plan import Plan
from jaynes.role import role, stop
from jaynes.runtime import TurnResult


class Note(BaseModel):
    text: str


LOOK = role("look", agent="planner")
LOOK.step("paint", output=Note, project="write", may_change=["**"])

SCREEN = role("screen", agent="planner")
SCREEN.step("build", output=Note, project="write", may_change=["**"])

NOTES = role("notes", agent="planner")  # no project: it delivers its outputs alone
NOTES.step("write", output=Note)

DIRECTION = role("direction", agent="planner", about="Plans a request that needs more than one role.")
DIRECTION.step("plan", output=Plan, project="read", stop=stop(0.2))  # a sample: the plan never waits for it

TOLD = role("told", agent="planner", about="Paints a look and asks the person whether it stays.")
PAINTED = TOLD.step("paint", output=Note, project="write", may_change=["**"], about="An agent paints the look.")
TOLD.step("pick", needs=[PAINTED], stop=stop(0.2, waits=True),
          about="You choose whether the look stays. Only its colours are kept; the screen is a sample.")

LOOK.about = "Paints a dark look."
SCREEN.about = "Builds a screen in the project's look."


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
        elif step == "plan":  # first a role nobody has, which Jaynes sends back; then the look and the screen
            first = json.loads((workspace / "step.json").read_text())["attempt"] == 1
            shown = [line[2:].split(":")[0] for line in prompt.splitlines() if line.startswith("- ") and ": " in line]
            (workspace / "output.json").write_text(json.dumps({"why": "roles shown: " + ", ".join(shown), "runs": [
                {"name": "look", "role": "painter" if first else "look", "task": "Paint a dark look"},
                {"name": "screen", "role": "screen", "task": "Build the home screen", "after": ["look"]}]}))
            return TurnResult(10, 1, "planner-1")
        else:
            text = "notes"
        heard = [line for line in prompt.splitlines() if line.startswith("- ") and "run r" in line]
        (workspace / "output.json").write_text(json.dumps({"text": " | ".join([text, *heard])}))
        return TurnResult(10, 1, "planner-1")
