"""A role that merges alone once its hidden case passes, and an agent for it, loaded by ``jaynes run`` and the page."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from jaynes import proof
from jaynes.role import Back, role, stop
from jaynes.runtime import TurnResult


class Made(BaseModel):
    summary: str


ROLE = role("merging", agent="quick", merges=True)
CASES = ROLE.step("cases", output=proof.Cases, project="read", hidden=True)
MAKE = ROLE.step("make", output=Made, project="write", may_change=["**"])
PROVE = ROLE.step("prove", output=proof.Proved, needs=[CASES, MAKE], run_by=proof.prove, project="read",
                  effect="read", max_turns=1, back=Back(MAKE, proof.failed, rounds=1))
ROLE.step("accept", needs=[PROVE, MAKE], stop=stop(0.05, sampled=False), question=lambda _o, _n: "Merge this?")


class Agent:
    name = "quick"

    def turn(self, workspace: Path, scratch: Path, prompt: str, deadline: float) -> TurnResult:
        step = json.loads((workspace / "step.json").read_text())["step"]
        if step == "cases":
            out = {"cases": [{"says": "Adding 2 and 2 gives 4",
                              "run": f"{sys.executable} -c 'from calc import add; assert add(2, 2) == 4'"}]}
        else:
            (workspace / "project" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
            out = {"summary": "add adds"}
        (workspace / "output.json").write_text(json.dumps(out))
        return TurnResult(10, 1, "quick-1")
