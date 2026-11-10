"""Fix a bug: first a test that fails because of it, then a fix that makes it pass with the rest of the suite.

Jaynes finds the command that runs the project's tests (``jaynes.runners``), or takes the one the person gave
at the start, so the agent cannot run less than the suite. The agent writes tests only and names their files;
Jaynes runs them in a box and requires that they fail, printing the failure the agent quotes. The person sees
the bug in plain examples, with the failure Jaynes saw, and decides whether the tests show it; the code stays
under Details. Then the agent changes the project, never its tests, until Jaynes sees those tests and the whole
suite pass. The person gives the final review, and the change comes back as a branch.

The project's suite must pass at its last commit, with its dependencies installed in the person's folder:
the box reaches no internet, only local ports, so nothing is installed during the run. A suite whose own
tests open a macOS box cannot run inside Jaynes's: macOS allows no box inside another.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from jaynes import project, proof, runners, suite
from jaynes.role import Back, Disputes, Fail, Pass, Shown, StepContext, Witnesses, role, stop
from jaynes.runners import Runner

TESTS = ("**/tests/**", "**/test/**", "**/__tests__/**", "**/spec/**", "**/test_*.py", "**/*_test.py",
         "**/*.test.*", "**/*.spec.*", "**/*_test.go", "**/*_spec.rb", "**/*Test.java", "**/*Tests.cs")


LONGEST = {"case": 50, "now": 70, "after": 70}  # characters of an example, as the person reads it at the stop
SUMMARY = 400  # characters of what changed, for the person at the merge


class Example(BaseModel):
    case: str = Field(description="a situation the person knows, in their words and with real values, such as "
                                  "'Event on 15 Nov, today 2 Nov'; no code or names from the code; at most 50 "
                                  "characters")
    now: str = Field(description="what the person sees today, because of the bug; at most 70 characters")
    after: str = Field(description="what the person will see once it is fixed; at most 70 characters")


class Reproduction(BaseModel):
    bug: str = Field(description="what goes wrong, in one or two sentences; when no test shows it, what you "
                                 "tried and why")
    reproduced: bool = Field(default=True, description="false when you found no bug a test can show: the code "
                                                       "behaves as it should, or the bug cannot be reproduced here; "
                                                       "then leave the fields below empty")
    tests: list[str] = Field(default_factory=list[str], description="the test files you added or changed, "
                                                                    "relative to the project folder")
    failure: str = Field(default="", description="a line the command prints because of the bug, copied exactly, "
                                                 "such as the assertion message")
    examples: list[Example] = Field(default_factory=list[Example],
                                    description="the bug as the person meets it, one example for each thing the "
                                                "tests show; the person judges the tests from these")


WRONG_TEST = ("empty, unless a test you may not change expects what the task changes: then name the test and what "
              "it should expect, and Jaynes sends it back to the agent that wrote the tests")


class Fix(BaseModel):
    summary: str = Field(description="what was wrong and what the person sees now, in two or three plain sentences "
                                     "for someone who does not read the code: commands they type, yes; names of "
                                     "functions, types or files, no; at most 400 characters")
    files: list[str]
    wrong_test: str = Field(default="", description=WRONG_TEST)


NO_RUNNER = ("no test runner was found in the project: Jaynes looks for pytest, a test script in package.json, "
             "go.mod, Cargo.toml and a Makefile test target. Start the run again with the command that runs the "
             "whole suite: --suite on jaynes run, or Test suite on New run")


def find_runner(ctx: StepContext) -> Runner:
    """The project's test runner, or the suite the person gave at the start."""
    assert ctx.needs.project is not None
    found = runners.detect(ctx.needs.project, str(ctx.started.get("suite") or ""))
    if found is None:
        raise LookupError(NO_RUNNER)
    return found


STATUS = {"fail", "failed", "failure", "failures", "error", "errors", "not", "ok", "exit", "status", "code", "test",
          "tests", "setup", "build"}  # words every failing run prints, whatever made it fail


_ran = suite.ran  # by this name, as the checks call it


def _quoted(failure: str, ran: suite.Ran, why: str) -> Fail | None:
    """None when ``failure`` is a line ``ran`` printed that says why it failed, not only that it did."""
    quote = failure.strip()
    if all(w in STATUS for w in re.findall(r"[a-z]+", quote.lower())):
        return Fail(f"{quote} names no reason; quote the line that says why the tests fail {why}, such as the "
                    f"assertion message or the missing name. The end of what it printed (exit {ran.code}):\n"
                    f"{ran.output}")
    if quote not in ran.printed:
        return Fail(f"{quote} is not in what the command printed; quote a line it prints {why}. The end of what "
                    f"it printed (exit {ran.code}):\n{ran.output}")
    return None


def fails_for_the_bug(out: Reproduction, needs: Witnesses) -> Pass | Fail:
    """The tests are test files, and their command fails, printing the failure quoted. Not reproduced passes:
    the person reads why at the stop, and accepting ends the run."""
    assert needs.project is not None
    if not out.bug.strip():
        return Fail("say what goes wrong, or, when no test shows it, what you tried and why")
    if not out.reproduced:
        return Pass()
    reasons = [f"{name} is empty; a reproduced bug needs it" for name in ("tests", "failure", "examples")
               if not getattr(out, name)]
    reasons += [f"the {field} of example {n} is {len(text)} characters, at most {most}: the person reads it at a glance"
                for n, e in enumerate(out.examples, 1) for field, most in LONGEST.items()
                if len(text := getattr(e, field).strip()) > most]
    if reasons:
        return Fail(*reasons)
    reasons = [f"{t} is not a test file; tests go in paths such as tests/ or *_test.*" for t in out.tests
               if project.allowed([t], TESTS)]
    reasons += [f"{t} does not exist in the project" for t in out.tests if not (needs.project / t).is_file()]
    if reasons:
        return Fail(*reasons)
    command = runners.for_files(needs[RUNNER].value, out.tests)
    ran = _ran(needs, command)
    if isinstance(ran, Fail):
        return ran
    if ran.code == 0:
        return Fail(f"the tests pass before any fix; they must fail because of the bug. {command} printed:\n"
                    + ran.output)
    return _quoted(out.failure, ran, "because of the bug") or Pass()


def speak_up(wrong_test: str, tests_step: str) -> None:
    """An agent that may not touch the tests says which one stands in its way: the step that wrote it runs again
    with what the agent said, and the person chooses only when the two agents still disagree."""
    if wrong_test.strip():
        raise Disputes(tests_step, wrong_test, about="test")


def fixed(out: Fix, needs: Witnesses) -> Pass | Fail:
    """The reproduction's tests pass, and then the whole suite does."""
    speak_up(out.wrong_test, REPRODUCE.name)
    if len(out.summary) > SUMMARY:
        return Fail(f"the summary is {len(out.summary)} characters, at most {SUMMARY}: the person reads it at the "
                    "merge in two or three sentences; the code says the rest")
    found: Reproduction = needs[REPRODUCE].value
    runner: Runner = needs[RUNNER].value
    for what, command in (("the tests still fail", runners.for_files(runner, found.tests)),
                          ("the suite fails", runner.suite)):
        ran = _ran(needs, command)
        if isinstance(ran, Fail):
            return ran
        if ran.code != 0:
            return Fail(f"{what}: {command} exited with {ran.code}. The end of what it printed:\n{ran.output}")
    return Pass()


def sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?", "…")) else f"{text}."


def shortened(summary: str) -> str:
    """A summary written before the limit: its first words; the whole stays under Details."""
    text = summary.strip()
    return text if len(text) <= SUMMARY else text[:SUMMARY + 1].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def changed_files(files: list[str]) -> Shown:
    """The files as the person finds them: the agent names them from its own folder."""
    named = [f.removeprefix("project/") for f in files]
    return Shown("Files changed", sentence(", ".join(named)) if named else "None named.", more=True)


def _examples(found: Reproduction, more: bool = False) -> list[Shown]:
    return [Shown(e.case.strip(), f"Now: {sentence(e.now)} After the fix: {sentence(e.after)}", more=more)
            for e in found.examples]


def ask_about_bug(out: Reproduction, _: Witnesses) -> str:
    return "Do these tests show the bug?" if out.reproduced else (
        f"No test shows the bug: {out.bug} Accept ends the run with nothing changed; return it with a hint to look "
        "again.")


def show_bug(out: Reproduction, _: Witnesses) -> list[Shown]:
    """The examples and the failure Jaynes saw in front; the agent's words and the files below, the code in Details."""
    if not out.reproduced:
        return []
    return [*_examples(out), Shown("What the test printed", out.failure.strip()),
            Shown("What goes wrong", sentence(out.bug), more=True),
            Shown("The tests", sentence(", ".join(out.tests)), more=True)]


def show_fixed(_: None, needs: Witnesses) -> list[Shown]:
    """What changed, in the agent's few sentences, that the tests pass, which Jaynes ran itself, and, folded, the
    examples and the files."""
    found: Reproduction = needs[REPRODUCE].value
    made: Fix = needs[FIX].value
    return [Shown("What changed", sentence(shortened(made.summary))),
            Shown("The bug's tests pass", "Jaynes ran them and the whole suite itself, in a box without internet."),
            *proof.shown_in(needs, PROVE), *_examples(found, more=True), changed_files(made.files)]


ROLE = role("fix-bug", merges=True,
            about="Fixes a bug the request describes: proves it with a failing test, then fixes it.")
# a run that finds no runner blocks at once with how to give one: trying again finds the same files
RUNNER = ROLE.step("runner", output=Runner, run_by=find_runner, project="read", max_turns=1)
# written from the task alone, before the work; only Jaynes and the person see it
CASES = ROLE.step("cases", output=proof.Cases, project="read", hidden=True, check=proof.written,
                  instructions=proof.WRITE, deadline=1800.0)
REPRODUCE = ROLE.step(
    "reproduce", output=Reproduction, needs=[RUNNER], project="write", may_change=TESTS, check=fails_for_the_bug,
    stop=stop(0.2), question=ask_about_bug, shows=show_bug, deadline=3600.0,
    ends=lambda out: not out.reproduced,
    instructions="Find the bug the task describes and write the smallest test that fails because of it, next to "
                 "the project's other tests and in their style. When the task lists what must hold, write a test "
                 "for each item now, even one that passes today: the fix may not touch the tests, so what you leave "
                 "out here goes unproven. If the fix will change what an existing test expects, change that test "
                 "now too. Change test files only; do not fix the bug yet. "
                 "inputs/runner.json says how Jaynes runs the project's tests: each followed by test files runs "
                 "only them, suite runs them all, and when each is empty the suite runs. Run your tests that way "
                 "yourself before you answer. Jaynes runs them again from the project folder in a box without "
                 "internet, with the dependencies already installed, and requires them to fail, printing the "
                 "line you quote. If you find no bug a test can show, set reproduced to false, say what you "
                 "tried and why in bug, and change nothing: the person decides whether the run ends. If the "
                 "person returned an earlier attempt, follow their note.")
FIX = ROLE.step(
    "fix", output=Fix, needs=[RUNNER, REPRODUCE], project="write", may_change=("**", *(f"!{t}" for t in TESTS)),
    check=fixed, deadline=3600.0,
    instructions="Fix the bug so the tests in inputs/reproduce.json pass, and the whole suite with them. Change "
                 "the project's code, never its tests: they are how the person knows the bug is gone. Keep the "
                 "change as small as the fix needs. Run the tests and the suite yourself as inputs/runner.json "
                 "says; Jaynes runs them again in a box without internet. If a test expects what the task changes, "
                 "say so in wrong_test; never bend the code to satisfy it, such as adding what the test looks for.")
PROVE = ROLE.step("prove", output=proof.Proved, needs=[CASES, FIX], run_by=proof.prove, project="read",
                  effect="read", max_turns=1, back=Back(FIX, proof.failed))
# fix last among the needs: a return from the merge goes to it
ROLE.step("accept", needs=[REPRODUCE, PROVE, FIX], stop=stop(0.05, sampled=False), shows=show_fixed,
          question=lambda _out, _needs: "Merge this fix?")
