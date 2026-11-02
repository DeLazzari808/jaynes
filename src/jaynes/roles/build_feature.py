"""Build a feature the person agreed on: first tests that fail because it does not exist yet, then code that makes
them pass with the rest of the suite.

The task is the plan the person and the agent agreed on in conversation. The agent writes tests only and names
their files; Jaynes runs them in a box and requires that they fail, printing the line the agent quotes. The person
sees the tests and decides whether they describe what was agreed: a misunderstanding costs least here, before any
code. Then the agent changes the project, never its tests, until Jaynes sees those tests and the whole suite pass.
The person gives the final review, and the change comes back as a branch. The suite needs the same as fix-bug's.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from jaynes import project, runners
from jaynes.role import Fail, Pass, Witnesses, role, stop
from jaynes.roles import fix_bug as fb
from jaynes.runners import Runner


class Spec(BaseModel):
    feature: str = Field(description="what the tests show the feature does, in one or two sentences, in the "
                                     "person's words from the plan")
    tests: list[str] = Field(description="the test files you added or changed, relative to the project folder")
    failure: str = Field(description="a line the command prints because the feature does not exist yet, copied "
                                     "exactly, such as the assertion message or the missing name")


class Built(BaseModel):
    summary: str = Field(description="what changed and how it does what the plan asked, for the person reviewing it")
    files: list[str]


def fails_first(out: Spec, needs: Witnesses) -> Pass | Fail:
    """The tests are test files, and their command fails, printing the failure quoted."""
    assert needs.project is not None
    reasons = [f"{name} is empty" for name in ("feature", "tests", "failure") if not getattr(out, name)]
    if reasons:
        return Fail(*reasons)
    reasons = [f"{t} is not a test file; tests go in paths such as tests/ or *_test.*" for t in out.tests
               if project.allowed([t], fb.TESTS)]
    reasons += [f"{t} does not exist in the project" for t in out.tests if not (needs.project / t).is_file()]
    if reasons:
        return Fail(*reasons)
    command = runners.for_files(needs[RUNNER].value, out.tests)
    ran = fb._ran(needs, command)  # pyright: ignore[reportPrivateUsage]
    if isinstance(ran, Fail):
        return ran
    if ran.code == 0:
        return Fail(f"the tests pass before the feature exists; they must fail until it is built. {command} "
                    "printed:\n" + ran.output)
    return fb._quoted(out.failure, ran, "because the feature does not exist yet") or Pass()  # pyright: ignore[reportPrivateUsage]


def built(out: Built, needs: Witnesses) -> Pass | Fail:
    """The agreed tests pass, and then the whole suite does."""
    agreed: Spec = needs[SPECIFY].value
    runner: Runner = needs[RUNNER].value
    for what, command in (("the tests still fail", runners.for_files(runner, agreed.tests)),
                          ("the suite fails", runner.suite)):
        ran = fb._ran(needs, command)  # pyright: ignore[reportPrivateUsage]
        if isinstance(ran, Fail):
            return ran
        if ran.code != 0:
            return Fail(f"{what}: {command} exited with {ran.code}. The end of what it printed:\n{ran.output}")
    return Pass()


ROLE = role("build-feature")
RUNNER = ROLE.step("runner", output=Runner, run_by=fb.find_runner, project="read", max_turns=1)
SPECIFY = ROLE.step(
    "specify", output=Spec, needs=[RUNNER], project="write", may_change=fb.TESTS, check=fails_first,
    stop=stop(0.2), question=lambda out, _: f"Do these tests describe what we agreed? {out.feature}",
    instructions="The task is the plan the person agreed on. Write the smallest tests that show the feature does "
                 "what the plan says, next to the project's other tests and in their style. They must fail now, "
                 "because the feature does not exist yet. Change test files only; do not build the feature yet. "
                 "inputs/runner.json says how Jaynes runs the project's tests: each followed by test files runs "
                 "only them, suite runs them all, and when each is empty the suite runs. Run your tests that way "
                 "yourself before you answer. Jaynes runs them again from the project folder in a box without "
                 "internet, with the dependencies already installed, and requires them to fail, printing the line "
                 "you quote. If the person returned an earlier attempt, follow their note.")
BUILD = ROLE.step(
    "build", output=Built, needs=[RUNNER, SPECIFY], project="write", may_change=("**", *(f"!{t}" for t in fb.TESTS)),
    check=built, deadline=3600.0,
    instructions="Build the feature so the tests in inputs/specify.json pass, and the whole suite with them. Change "
                 "the project's code, never its tests: they are what the person agreed to. Keep the change as small "
                 "as the plan needs. Run the tests and the suite yourself as inputs/runner.json says; Jaynes runs "
                 "them again in a box without internet.")
ROLE.step("accept", needs=[BUILD], stop=stop(0.05, sampled=False),
          question=lambda _, needs: f"Merge this feature? {needs[BUILD].value.summary}")
