"""Build a feature the person agreed on: first tests that fail because it does not exist yet, then code that makes
them pass with the rest of the suite.

The task is the plan the person and the agent agreed on in conversation. The agent writes tests only and names
their files; Jaynes runs them in a box and requires that they fail, printing the line the agent quotes. The person
sees a few examples of what the tests check, with a sketch of the screen when an example shows one, and decides
whether they describe what was agreed: a misunderstanding costs least here, before any code. The agent writes each
sketch as a page; Jaynes draws it with its own Chrome, which reaches nothing but that page. Then the agent changes the project, never its tests, until Jaynes sees those tests and the whole suite pass.
The person gives the final review, and the change comes back as a branch. The suite needs the same as fix-bug's.
"""

from __future__ import annotations

import functools
import http.server
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field

from jaynes import project, runners
from jaynes.browser import BrowserError, open_page
from jaynes.role import (
    Blocked,
    Fail,
    Pass,
    Picture,
    Shown,
    StepContext,
    Witnesses,
    role,
    stop,
)
from jaynes.roles import fix_bug as fb
from jaynes.roles.references import chrome
from jaynes.runners import Runner

LONGEST = {"case": 50, "result": 70}  # characters: together, one line the person reads at a glance in the terminal
SCREEN = 40_000  # characters of a sketch's page
SKETCH_SIZE = (1280, 800)  # the sketch's window; the picture is as tall as the page, up to TALLEST
TALLEST = 2400


class Example(BaseModel):
    case: str = Field(description="a situation the person knows, in their words and with real values, such as "
                                  "'Event on 15 Nov, today 2 Nov'; no code or names from the code; at most 50 "
                                  "characters")
    result: str = Field(description="what the person sees then, such as 'it shows 13 days left'; at most 70 "
                                    "characters")
    test: str = Field(description="the name of the test that checks this, as written in the test file")
    screen: str = Field("", description="when the person sees this on a screen: that screen as you will build it, "
                                        "with this example's values, as one HTML page with its styles inline, "
                                        "nothing loaded from elsewhere; empty when nothing shows on a screen")


class Spec(BaseModel):
    examples: list[Example] = Field(description="one to five examples of what the tests check, the ones the person "
                                                "would look at to tell whether this is what they asked for")
    feature: str = Field(description="what the tests show the feature does, in one or two sentences, in the "
                                     "person's words from the plan")
    tests: list[str] = Field(description="the test files you added or changed, relative to the project folder")
    failure: str = Field(description="a line the command prints because the feature does not exist yet, copied "
                                     "exactly, such as the assertion message or the missing name")


class Sketched(BaseModel):
    pictures: list[str] = Field(description="the picture of each example's screen among the step's files, in the "
                                            "order of the examples; empty for an example with no screen")


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
    quoted = fb._quoted(out.failure, ran, "because the feature does not exist yet")  # pyright: ignore[reportPrivateUsage]
    if quoted:
        return quoted
    if not 1 <= len(out.examples) <= 5:
        return Fail(f"give one to five examples of what the tests check, not {len(out.examples)}: the person "
                    "decides from them")
    text = "\n".join((needs.project / t).read_text(errors="replace") for t in out.tests)
    reasons = [f"{e.test} is not in {', '.join(out.tests)}; name the test that checks {e.case!r}"
               for e in out.examples if not e.test.strip() or e.test.strip() not in text]
    reasons += [f"the {part} of example {n} is {len(text)} characters, at most {most}: one situation and what "
                "the person sees, in a line they read at a glance; put the rest in feature"
                for n, e in enumerate(out.examples, 1) for part, most in LONGEST.items()
                if len(text := getattr(e, part).strip()) > most]
    reasons += [f"the screen of example {n} is {len(e.screen)} characters, at most {SCREEN}: draw only what the "
                "person sees in that example" for n, e in enumerate(out.examples, 1) if len(e.screen) > SCREEN]
    if reasons:
        return Fail(*reasons)
    return Pass()


@contextmanager
def _served(folder: Path) -> Iterator[str]:
    """The files in ``folder`` on a loopback address, for Jaynes's Chrome only."""
    handler = functools.partial(_Quiet, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def draw_sketches(ctx: StepContext) -> Sketched:
    """Each example's screen as a picture, drawn by Jaynes's Chrome from the page the agent wrote. The page reaches
    nothing but itself: every other request is refused."""
    agreed: Spec = ctx.needs[SPECIFY].value
    pages = {n: e.screen for n, e in enumerate(agreed.examples, 1) if e.screen.strip()}
    if not pages:
        return Sketched(pictures=["" for _ in agreed.examples])
    if chrome() is None:
        raise Blocked("Jaynes draws the sketches of the screens with Google Chrome and found none; install it or "
                      "set JAYNES_CHROME")
    files = ctx.workspace / "files"
    files.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jaynes-sketch-") as tmp:
        for n, html in pages.items():
            (Path(tmp) / f"example-{n}.html").write_text(html)
        try:
            with _served(Path(tmp)) as origin, open_page(origin) as page:
                for n in pages:
                    page.view("light", SKETCH_SIZE)
                    page.goto(f"/example-{n}.html")
                    tall = page.evaluate("document.documentElement.scrollHeight")
                    height = min(max(int(tall or 0), SKETCH_SIZE[1]), TALLEST)
                    page.view("light", (SKETCH_SIZE[0], height))
                    page.screenshot(files / f"example-{n}.png")
        except BrowserError as err:
            raise Blocked(f"Jaynes's Chrome could not draw the sketches: {err}") from None
    return Sketched(pictures=[f"example-{n}.png" if n in pages else "" for n in range(1, len(agreed.examples) + 1)])


def show_examples(_: None, needs: Witnesses) -> list[Shown]:
    """The examples in front, each with its sketch, as the person judges them; what the agent says the tests cover,
    below."""
    out: Spec = needs[SPECIFY].value
    pictures: list[str] = needs[SKETCH].value.pictures
    def sentence(text: str) -> str:
        text = text.strip()
        return text if text.endswith((".", "!", "?")) else f"{text}."
    def drawn(n: int) -> tuple[Picture, ...]:
        return (Picture(SKETCH.name, pictures[n], "sketch"),) if n < len(pictures) and pictures[n] else ()
    return [*(Shown(e.case.strip(), sentence(e.result), drawn(n)) for n, e in enumerate(out.examples)),
            Shown("What the tests cover", f"{sentence(out.feature)} In {', '.join(out.tests)}.", more=True)]


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
    instructions="The task is the plan the person agreed on. Write the smallest tests that show the feature does "
                 "what the plan says, next to the project's other tests and in their style. They must fail now, "
                 "because the feature does not exist yet. Change test files only; do not build the feature yet. "
                 "inputs/runner.json says how Jaynes runs the project's tests: each followed by test files runs "
                 "only them, suite runs them all, and when each is empty the suite runs. Run your tests that way "
                 "yourself before you answer. Jaynes runs them again from the project folder in a box without "
                 "internet, with the dependencies already installed, and requires them to fail, printing the line "
                 "you quote. Then give one to five examples of what they check, in the person's words with real "
                 "values and no code, each naming its test: the person decides from them. Keep each short: the "
                 "situation in at most 50 characters, what they see in at most 70. When an example shows "
                 "something on a screen, draw that screen in its screen field as you will build it, with the "
                 "example's values: Jaynes turns it into a picture beside the example. If the person returned "
                 "an earlier attempt, follow their note.")
SKETCH = ROLE.step("sketch", output=Sketched, needs=[SPECIFY], run_by=draw_sketches, max_turns=1)
# the stop has no output of its own, so a return goes back to specify, its last need
ROLE.step("agree", needs=[SKETCH, SPECIFY], stop=stop(0.2),
          question=lambda _out, _needs: "Do these examples show what you asked for?", shows=show_examples)
BUILD = ROLE.step(
    "build", output=Built, needs=[RUNNER, SPECIFY], project="write", may_change=("**", *(f"!{t}" for t in fb.TESTS)),
    check=built, deadline=3600.0,
    instructions="Build the feature so the tests in inputs/specify.json pass, and the whole suite with them. Change "
                 "the project's code, never its tests: they are what the person agreed to. Keep the change as small "
                 "as the plan needs. Run the tests and the suite yourself as inputs/runner.json says; Jaynes runs "
                 "them again in a box without internet.")
ROLE.step("accept", needs=[BUILD], stop=stop(0.05, sampled=False),
          question=lambda _, needs: f"Merge this feature? {needs[BUILD].value.summary}")
