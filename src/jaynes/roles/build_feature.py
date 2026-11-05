"""Build a feature the person agreed on: first tests that fail because it does not exist yet, then code that makes
them pass with the rest of the suite.

The task is the plan the person and the agent agreed on in conversation. The agent writes tests only and names
their files; Jaynes runs them in a box and requires that they fail, printing the line the agent quotes. The person
sees a few examples of what the tests check, with a sketch of the screen when an example shows one, and decides
whether they describe what was agreed: a misunderstanding costs least here, before any code. The agent writes each
sketch as a page; Jaynes draws it with its own Chrome, which reaches nothing but that page. Then the agent
changes the project, never its tests, until Jaynes sees those tests and the whole suite pass. The person gives the
final review, and the change comes back as a branch. The suite needs the same as fix-bug's.

A feature keeps the project's visual identity: the colours and fonts its files already set. Jaynes reads them at
the start; a sketch or a built screen that brings others is refused, since changing the identity is
visual-identity's work, where the person sees the direction first.
"""

from __future__ import annotations

import functools
import http.server
import re
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field

from jaynes import project, runners, screens
from jaynes.browser import BrowserError, Page, open_page
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
from jaynes.roles.fix_bug import SUMMARY, sentence
from jaynes.roles.references import chrome
from jaynes.runners import Runner

LONGEST = {"case": 50, "result": 70}  # characters: together, one line the person reads at a glance in the terminal
SCREEN = 40_000  # characters of a sketch's page
SKETCH_SIZE = (1280, 800)  # the window a screen is laid out in; its picture is as tall as what it shows
SHORTEST, TALLEST, MARGIN = 160, 2400, 24
# where what the page shows ends: a body can fill the window with nothing in it
CONTENT_BOTTOM = "(() => { const r = document.createRange(); r.selectNodeContents(document.body); " \
                 "return Math.ceil(r.getBoundingClientRect().bottom); })()"
STYLED = ("color:", "background", "font-family", "<style")  # a file that sets how something looks has one of these
STYLE_SUFFIXES = {".css", ".scss", ".sass", ".less", ".html", ".htm", ".tmpl", ".gohtml", ".templ", ".vue", ".svelte",
                  ".jsx", ".tsx", ".js", ".ts", ".go", ".py", ".rb", ".erb", ".php", ".astro", ".svg"}
NOT_THE_PROJECT = {"node_modules", "vendor", ".git", "dist", "build", "target", ".venv"}
COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}){1,2}\b|(?:rgba?|hsla?)\([^)]*\)", re.IGNORECASE)
FONT = re.compile(r"font-family\s*:\s*([^;}\n]+)", re.IGNORECASE)
GENERIC_FONTS = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif", "ui-serif",
                 "ui-monospace", "ui-rounded", "emoji", "math", "fangsong", "inherit", "initial", "unset"}


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
    page: str = Field("", description="when the run has a preview and this shows on a screen: the path of that screen "
                                      "on the running product, such as /t/charter?tab=team; Jaynes photographs it "
                                      "for the merge")


class Spec(BaseModel):
    examples: list[Example] = Field(description="one to five examples of what the tests check, the ones the person "
                                                "would look at to tell whether this is what they asked for")
    feature: str = Field(description="what the tests show the feature does, in one or two sentences, in the "
                                     "person's words from the plan")
    tests: list[str] = Field(description="the test files you added or changed, relative to the project folder")
    failure: str = Field(description="a line the command prints because the feature does not exist yet, copied "
                                     "exactly, such as the assertion message or the missing name")


class Identity(BaseModel):
    """The colours and fonts the project's files already set, as Jaynes read them; empty when it sets none."""

    colors: list[str] = Field(description="hex colours in six lowercase digits, and rgb() or hsl() values")
    fonts: list[str] = Field(description="the first family of each font-family, lowercase; generic ones left out")
    files: list[str] = Field(description="the project files they were read from")


class Sketched(BaseModel):
    pictures: list[str] = Field(description="the picture of each example's screen among the step's files, in the "
                                            "order of the examples; empty for an example with no screen")


class Looked(BaseModel):
    pictures: list[str] = Field(description="the picture of each example on the real screen among the step's files, "
                                            "in the order of the examples; empty where there is none")


class Built(BaseModel):
    summary: str = Field(description="what the person can do now that they could not before, in two or three plain "
                                     "sentences for someone who does not read the code: commands they type, yes; "
                                     "names of functions, types or files, no; at most 400 characters")
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
    if needs.started.get("preview"):
        reasons += [f"example {n} shows a screen and names no page: give the path of that screen on the running "
                    "product, so Jaynes can photograph it for the merge" for n, e in enumerate(out.examples, 1)
                    if e.screen.strip() and not e.page.strip()]
    reasons += [f"{e.page} is not a path on the product: give one that starts with /, such as /t/charter"
                for e in out.examples if e.page.strip() and not e.page.strip().startswith("/")]
    identity: Identity = needs[IDENTITY].value
    reasons += [f"the screen of example {n} uses {', '.join(strange)}, which the project's identity does not have; "
                f"{_has(identity)}" for n, e in enumerate(out.examples, 1)
                if (strange := _outside(*_literals(e.screen), identity))]
    if reasons:
        return Fail(*reasons)
    return Pass()


def _literals(text: str) -> tuple[set[str], set[str]]:
    """The colours and the first font families a text sets, normalised."""
    colors = {_color(m) for m in COLOR.findall(text)}
    fonts = {f for m in FONT.findall(text) if (f := m.split(",")[0].strip().strip("'\"").lower())
             and f not in GENERIC_FONTS and not f.startswith("var(")}
    return colors, fonts


def _color(literal: str) -> str:
    if literal.startswith("#"):
        h = literal[1:].lower()
        return "#" + ("".join(c * 2 for c in h) if len(h) == 3 else h)
    return re.sub(r"\s+", "", literal.lower())


def _outside(colors: set[str], fonts: set[str], identity: Identity) -> list[str]:
    """What is set that the identity does not have: colours when it has colours, fonts when it has fonts."""
    return (sorted(colors - set(identity.colors)) if identity.colors else []) + \
        (sorted(fonts - set(identity.fonts)) if identity.fonts else [])


def _has(identity: Identity) -> str:
    return (f"it has {', '.join(identity.colors + identity.fonts)} in {', '.join(identity.files)}. Use those, or "
            "change the identity with visual-identity")


def _styled(root: Path) -> dict[str, tuple[set[str], set[str]]]:
    """What each file of the project that sets how something looks sets, by path; tests and vendored code left out."""
    out: dict[str, tuple[set[str], set[str]]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if (NOT_THE_PROJECT & set(rel.parts) or path.suffix.lower() not in STYLE_SUFFIXES or not path.is_file()
                or path.stat().st_size > 1_000_000 or not project.allowed([rel.as_posix()], fb.TESTS)):
            continue
        text = path.read_text(errors="replace")
        if any(w in text.lower() for w in STYLED):
            colors, fonts = _literals(text)
            if colors or fonts:
                out[rel.as_posix()] = (colors, fonts)
    return out


def read_identity(ctx: StepContext) -> Identity:
    """The colours and fonts the project sets today, read by Jaynes before any agent works."""
    assert ctx.needs.project is not None
    styled = _styled(ctx.needs.project)
    return Identity(colors=sorted(set[str]().union(*(c for c, _ in styled.values()))),
                    fonts=sorted(set[str]().union(*(f for _, f in styled.values()))), files=list(styled))


def keeps_identity(_: Built, needs: Witnesses) -> Pass | Fail:
    """No file of the built project sets a colour or font the identity does not have."""
    assert needs.project is not None
    identity: Identity = needs[IDENTITY].value
    reasons = [f"{rel} brings {', '.join(strange)}, which the project's identity does not have; {_has(identity)}"
               for rel, (colors, fonts) in _styled(needs.project).items()
               if (strange := _outside(colors, fonts, identity))]
    return Fail(*reasons[:20]) if reasons else Pass()


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


def _photograph(page: Page, path: str, file: Path) -> None:
    """The page at ``path``, as tall as what it shows, up to TALLEST: a short screen is a short picture."""
    page.view("light", SKETCH_SIZE)
    page.goto(path)
    tall = page.evaluate(CONTENT_BOTTOM)
    page.view("light", (SKETCH_SIZE[0], min(max(int(tall or 0) + MARGIN, SHORTEST), TALLEST)))
    page.screenshot(file)


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
                    _photograph(page, f"/example-{n}.html", files / f"example-{n}.png")
        except BrowserError as err:
            raise Blocked(f"Jaynes's Chrome could not draw the sketches: {err}") from None
    return Sketched(pictures=[f"example-{n}.png" if n in pages else "" for n in range(1, len(agreed.examples) + 1)])


def show_examples(_: None, needs: Witnesses) -> list[Shown]:
    """The examples in front, each with its sketch, as the person judges them; what the agent says the tests cover,
    below."""
    out: Spec = needs[SPECIFY].value
    pictures: list[str] = needs[SKETCH].value.pictures
    def drawn(n: int) -> tuple[Picture, ...]:
        return (Picture(SKETCH.name, pictures[n], "sketch"),) if n < len(pictures) and pictures[n] else ()
    identity: Identity = needs[IDENTITY].value
    def count(n: int, word: str) -> str:
        return f"{n} {word}{'s' * (n != 1)}"
    about = Shown("The project's identity", f"{count(len(identity.colors), 'colour')} and "
                  f"{count(len(identity.fonts), 'font')}, in {', '.join(identity.files)}; the screens use only "
                  "those.", more=True) if identity.colors or identity.fonts else \
        Shown("No identity yet", "The screens look as the agent drew them, until visual-identity runs.", more=True)
    screens_shown = any(e.screen.strip() for e in out.examples)
    return [*(Shown(e.case.strip(), sentence(e.result), drawn(n)) for n, e in enumerate(out.examples)),
            Shown("What the tests cover", f"{sentence(out.feature)} In {', '.join(out.tests)}.", more=True),
            *([about] if screens_shown else [])]


def look_at_screens(ctx: StepContext) -> Looked:
    """Each example on the real screen: the built project served in a box, photographed by Jaynes's Chrome."""
    agreed: Spec = ctx.needs[SPECIFY].value
    pages = {n: e.page.strip() for n, e in enumerate(agreed.examples, 1) if e.page.strip()}
    if not pages or not ctx.started.get("preview") or ctx.needs.project is None:
        return Looked(pictures=["" for _ in agreed.examples])
    if chrome() is None:
        raise Blocked("Jaynes looks at the screens with Google Chrome and found none; install it or set JAYNES_CHROME")
    files = ctx.workspace / "files"
    files.mkdir(exist_ok=True)
    try:
        with screens.serve(ctx.needs.project, ctx.started, ctx.needs.source, ctx.needs.hidden) as origin, \
                open_page(origin, deny=screens.denied(ctx.started)) as page:
            for n, path in pages.items():
                _photograph(page, path, files / f"screen-{n}.png")
    except BrowserError as err:
        raise Blocked(f"Jaynes's Chrome could not look at the product: {err}") from None
    return Looked(pictures=[f"screen-{n}.png" if n in pages else "" for n in range(1, len(agreed.examples) + 1)])


def show_built(_: None, needs: Witnesses) -> list[Shown]:
    """What changed, in the agent's few sentences, that the agreed examples pass, which Jaynes ran itself, each one on
    the real screen, and, folded, the ones with no picture and the files."""
    made: Built = needs[BUILD].value
    agreed: Spec = needs[SPECIFY].value
    pictures: list[str] = needs[LOOK].value.pictures
    pictured = dict(enumerate(pictures))
    seen = [Shown(e.case.strip(), sentence(e.result), (Picture(LOOK.name, pictured[n], "the real screen"),))
            for n, e in enumerate(agreed.examples) if pictured.get(n)]
    unseen = [Shown(e.case.strip(), sentence(e.result), more=True)
              for n, e in enumerate(agreed.examples) if not pictured.get(n)]
    n = len(agreed.examples)
    passed = Shown("The agreed example passes" if n == 1 else f"All {n} agreed examples pass",
                   f"Jaynes ran {'its test' if n == 1 else 'their tests'} and the whole suite itself, in a box without "
                   "internet.")
    if not seen and any(e.screen.strip() for e in agreed.examples):
        seen = [Shown("No pictures of the real screens", "This run has no preview; a run with --preview shows them "
                                                         "here, as Jaynes sees them.")]
    return [Shown("What changed", sentence(fb.shortened(made.summary))), passed, *seen, *unseen,
            fb.changed_files(made.files)]


def built(out: Built, needs: Witnesses) -> Pass | Fail:
    """The agreed tests pass, and then the whole suite does."""
    if len(out.summary) > SUMMARY:
        return Fail(f"the summary is {len(out.summary)} characters, at most {SUMMARY}: the person reads it at the "
                    "merge in two or three sentences; the code says the rest")
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
IDENTITY = ROLE.step("identity", output=Identity, run_by=read_identity, project="read", max_turns=1)
SPECIFY = ROLE.step(
    "specify", output=Spec, needs=[RUNNER, IDENTITY], project="write", may_change=fb.TESTS, check=fails_first,
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
                 "example's values: Jaynes turns it into a picture beside the example. When the run has a "
                 "preview, say in page where that screen will be on the running product: Jaynes photographs it "
                 "there for the merge. A screen uses only the "
                 "colours and fonts in inputs/identity.json, the project's identity, as its variables or values; "
                 "when it lists none, draw plainly. If the person returned an earlier attempt, follow their note.")
SKETCH = ROLE.step("sketch", output=Sketched, needs=[SPECIFY], run_by=draw_sketches, max_turns=1)
# the stop has no output of its own, so a return goes back to specify, its last need
ROLE.step("agree", needs=[SKETCH, SPECIFY, IDENTITY], stop=stop(0.2),
          question=lambda _out, _needs: "Do these examples show what you asked for?", shows=show_examples)
BUILD = ROLE.step(
    "build", output=Built, needs=[RUNNER, SPECIFY, IDENTITY], project="write",
    may_change=("**", *(f"!{t}" for t in fb.TESTS)), check=[built, keeps_identity], deadline=3600.0,
    instructions="Build the feature so the tests in inputs/specify.json pass, and the whole suite with them. Change "
                 "the project's code, never its tests: they are what the person agreed to. Keep the change as small "
                 "as the plan needs. Run the tests and the suite yourself as inputs/runner.json says; Jaynes runs "
                 "them again in a box without internet. Screens use only the colours and fonts in "
                 "inputs/identity.json, as the project's variables where it has them; Jaynes refuses a build that "
                 "brings others, since changing the identity is visual-identity's work.")
LOOK = ROLE.step("look", output=Looked, needs=[SPECIFY, BUILD], run_by=look_at_screens, project="read", effect="read",
                 max_turns=1)
# build last among the needs: a return from the merge goes to it
ROLE.step("accept", needs=[SPECIFY, LOOK, BUILD], stop=stop(0.05, sampled=False), shows=show_built,
          question=lambda _out, _needs: "Merge this feature?")
