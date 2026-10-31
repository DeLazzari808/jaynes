"""A small change, the light path: the agent changes the project and Jaynes runs the suite, with no stop.

The agent chooses this role when the agreed plan is small. Jaynes checks that choice against the change itself: a
diff of more than ``LIMIT`` changed lines, or one that touches a path that runs later, stops for a person, who
merges it or returns it. A project with no test runner Jaynes can find runs no suite; the gate still watches every
command. The change comes back as a branch.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from jaynes import runners
from jaynes.role import Fail, Pass, StepContext, Witnesses, role, stop
from jaynes.roles import fix_bug as fb

LIMIT = 30  # changed lines, added and removed, past which a person sees the change


class Suite(BaseModel):
    command: str = Field(description="runs the project's whole test suite; empty when the project has none")


class Changed(BaseModel):
    summary: str = Field(description="what changed, in a sentence or two, for the person reviewing it")
    files: list[str]


def find_suite(ctx: StepContext) -> Suite:
    """The project's suite, the one the person gave at the start, or none."""
    assert ctx.needs.project is not None
    found = runners.detect(ctx.needs.project, str(ctx.started.get("suite") or ""))
    return Suite(command=found.suite if found else "")


def passes(out: Changed, needs: Witnesses) -> Pass | Fail:
    """The whole suite passes, when the project has one."""
    command = needs[RUNNER].value.command
    if not command:
        return Pass()
    ran = fb._ran(needs, command)  # pyright: ignore[reportPrivateUsage]
    if isinstance(ran, Fail):
        return ran
    if ran.code != 0:
        return Fail(f"the suite fails: {command} exited with {ran.code}. The end of what it printed:\n{ran.output}")
    return Pass()


def lines(patch: str) -> int:
    """Lines a diff adds or removes, without its file headers."""
    return sum(1 for x in patch.splitlines() if x[:1] in ("+", "-") and not x.startswith(("+++", "---")))


def bigger(out: Changed, patch: str) -> bool:
    return lines(patch) > LIMIT


ROLE = role("small-change")
RUNNER = ROLE.step("runner", output=Suite, run_by=find_suite, project="read", max_turns=1)
ROLE.step(
    "change", output=Changed, needs=[RUNNER], project="write", may_change=("**",), check=passes, deadline=3600.0,
    stop=stop(0.05, sampled=False), asks=bigger,
    question=lambda out, _: f"This change is bigger than a small one. Merge it? {out.summary}",
    instructions="The task is a small change the person agreed on. Make it, keeping it as small as the task needs; "
                 f"past {LIMIT} changed lines, the person reviews it. inputs/runner.json holds the command that runs "
                 "the project's whole suite, empty when it has none: run it yourself before you answer. Jaynes runs "
                 "it again in a box without internet and requires it to pass. If the person returned an earlier "
                 "attempt, follow their note.")
