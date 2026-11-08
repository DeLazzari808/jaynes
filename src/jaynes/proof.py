"""Proof the maker cannot see: cases a separate agent writes from the request alone, before the work starts, which
Jaynes runs against the work itself.

A role adds two steps. One is hidden (``role.step(..., hidden=True)``): an agent reads the task and the project as
it was, never the work, and writes ``Cases``, each a sentence a person reads and a shell command that exits 0 when
the sentence holds. No agent step receives it. The other is run by Jaynes after the work (``prove``): it serves the
changed project with the run's preview command, in a box with no internet, and runs each command in a box over a
copy of the project, with the served app at ``$JAYNES_URL``. Its output is ``Proved``.

When a case fails, the step that made the work goes back with the sentence alone (``failed``, for ``Back``), never
the command, twice at most; then the run goes on, the failures show at the stop, and nothing merges by itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel, Field

from jaynes import sandbox, suite
from jaynes.role import Blocked, Fail, Pass, Shown, Step, StepContext, Witnesses

URL = "JAYNES_URL"
CASE_SECONDS = 120.0

WRITE = (
    "You write the checks a change will be judged by, before anyone makes it. You read only the task and the "
    "project as it is now; whoever makes the change never sees what you write, only the sentence of a check that "
    "fails. Write each case as a sentence a person reads, saying what must hold once the task is done, and a shell "
    "command that exits 0 when it holds and not otherwise. A command runs from the project's root, in a box with no "
    "internet, on the project with the change made. When the run serves the project (its preview), the app runs "
    f"at ${URL}; use curl -s against it. Check what the task states and can be seen from outside: an answer from "
    "the app, what a command prints or how it exits, what a file holds. Never check how it is built: no internal "
    "names the task does not give, no exact wording it does not give; match key words without case (grep -qi). "
    "A case about something new must fail on the project as it is now. At most eight cases, each one thing. Put "
    "in untestable, in a sentence each, what the task asks that no command can check from outside, such as how a "
    "screen looks."
)


class Case(BaseModel):
    says: str = Field(description="What must hold once the task is done, in one sentence a person reads")
    run: str = Field(description="A shell command that exits 0 when it holds; the served app is at $JAYNES_URL")


class Cases(BaseModel):
    cases: list[Case]
    untestable: list[str] = Field(default_factory=list[str],
                                  description="What the task asks that no command can check from outside")


class Result(BaseModel):
    says: str
    passed: bool
    ran: bool = True  # False when the case needs the served app and the run serves none
    output: str = ""  # the end of what the command printed; never shown to the maker


class Proved(BaseModel):
    results: list[Result]
    untestable: list[str] = Field(default_factory=list[str])
    served: bool = False  # the changed project was served and the cases ran against it

    @property
    def passed(self) -> bool:
        """Proof enough to merge alone: at least one case ran, and every case passed."""
        return bool(self.results) and all(r.passed for r in self.results)


def written(out: Cases, _: Witnesses) -> Pass | Fail:
    """Each case says something and runs something."""
    bad = [f"case {n} has no sentence" for n, c in enumerate(out.cases, 1) if not c.says.strip()]
    bad += [f"case {n} has no command" for n, c in enumerate(out.cases, 1) if not c.run.strip()]
    if len(out.cases) > 8:
        bad.append(f"{len(out.cases)} cases; write at most eight")
    return Fail(*bad) if bad else Pass()


def _cases(needs: Witnesses) -> Cases:
    found = [c.value for c in needs.all() if isinstance(c.value, Cases)]
    if len(found) != 1:
        raise Blocked("a proving step needs exactly one step that writes the hidden cases")
    return found[0]


def _uses_url(command: str) -> bool:
    return re.search(rf"\${{?{URL}\b", command) is not None


@contextmanager
def _served(ctx: StepContext) -> Iterator[str]:
    """The changed project served in a box, when the run has a preview; else nothing."""
    if not ctx.started.get("preview") or ctx.needs.project is None:
        yield ""
        return
    from jaynes import screens
    with screens.serve(ctx.needs.project, ctx.started, ctx.needs.source, ctx.needs.hidden) as origin:
        yield origin


def prove(ctx: StepContext) -> Proved:
    """Run every hidden case against the work: a trusted executor for a role's proving step."""
    cases = _cases(ctx.needs)
    if ctx.needs.project is None:
        raise Blocked("a proving step works on the project: give it project='read'")
    results: list[Result] = []
    with _served(ctx) as origin:
        for case in cases.cases:
            if _uses_url(case.run) and not origin:
                results.append(Result(says=case.says, passed=False, ran=False,
                                      output="this case needs the served app, and the run has no preview"))
                continue
            try:
                ran = suite.run(ctx.needs, case.run, timeout=CASE_SECONDS, env={URL: origin} if origin else {})
            except sandbox.Unavailable as err:
                raise Blocked(str(err)) from None
            results.append(Result(says=case.says, passed=ran.code == 0, output=ran.output[-1500:]))
    return Proved(results=results, untestable=list(cases.untestable), served=bool(origin))


def failed(out: Proved, _: Witnesses) -> list[str]:
    """What the maker is told when cases fail: the sentence of each, never its command."""
    return [f"A check you cannot see found that this does not hold: {r.says}" for r in out.results
            if r.ran and not r.passed]


def shown_in(needs: Witnesses, step: Step[Proved]) -> list[Shown]:
    """What the stop shows of the proof, or nothing for a run from before its role proved."""
    try:
        return [shown(needs[step].value)]
    except KeyError:
        return []


def shown(out: Proved) -> Shown:
    """What the stop shows of the proof: how many hidden cases passed, and each one's sentence."""
    if not out.results:
        return Shown("No hidden checks", "Nothing the request asks can be checked from outside" +
                     (f": {'; '.join(out.untestable)}" if out.untestable else "") + ". It merges only with your accept.")
    good = sum(r.passed for r in out.results)
    n = len(out.results)
    title = ("The hidden check passes" if good == n == 1 else f"All {n} hidden checks pass" if good == n
             else f"{good} of {n} hidden checks pass")
    where = "against the app Jaynes served from the change, " if out.served else ""
    lines = [f"Written from your request alone before the work, run by Jaynes {where}in a box without internet."]
    lines += [f"{'Passes' if r.passed else 'Fails' if r.ran else 'Not run'}: {r.says}" for r in out.results]
    return Shown(title, "\n".join(lines))
