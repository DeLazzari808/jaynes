"""The built-in build-feature role: tests of the agreed plan that fail first, then code that makes them and the suite
pass, with a person at the tests and at the merge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jaynes.conductor import Conductor
from jaynes.role import Checked, Fail, Pass, StepContext, Witnesses
from jaynes.roles import build_feature as bf
from jaynes.roles.references import chrome
from jaynes.verify import verify
from tests.fakes import Scripted
from tests.test_fix_bug import (  # noqa: F401  # pyright: ignore[reportUnusedImport]
    PYTHON,
    copy,
    edit,
    git,
    pytestmark,
    python,
    ran_by,
    repo,
)

TEST = "from calc import mul\n\n\ndef test_mul_multiplies():\n    assert mul(2, 3) == 6\n"
MUL = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"


CSS = ":root { --accent: #1E88E5; --bg: #fff; }\nbody { font-family: 'Inter', Arial, sans-serif; color: rgb(20, 20, 20); }\n"
IDENTITY = bf.Identity(colors=["#1e88e5", "#ffffff", "rgb(20,20,20)"], fonts=["inter"], files=["static/app.css"])
NONE = Checked("identity", bf.Identity(colors=[], fonts=[], files=[]), "h", "c")


def spec(**overrides: Any) -> bf.Spec:
    base: dict[str, Any] = {"feature": "mul multiplies two numbers", "tests": ["tests/test_mul.py"],
                            "failure": "cannot import name 'mul'",
                            "examples": [{"case": "Multiplying 2 by 3", "result": "gives 6",
                                          "test": "test_mul_multiplies"}]}
    return bf.Spec(**{**base, **overrides})


def test_the_tests_must_fail_before_the_feature_exists(repo: Path, tmp_path: Path) -> None:
    needs = Witnesses(ran_by(identity=NONE), copy(repo, tmp_path, {"tests/test_mul.py": TEST}), source=repo)
    assert isinstance(bf.fails_first(spec(), needs), Pass)
    passing = bf.fails_first(spec(tests=["tests/test_zero.py"]), needs)
    assert isinstance(passing, Fail) and "the tests pass before the feature exists" in passing.reasons[0]
    elsewhere = bf.fails_first(spec(tests=["calc.py"]), needs)
    assert isinstance(elsewhere, Fail) and "calc.py is not a test file" in elsewhere.reasons[0]
    quoted = bf.fails_first(spec(failure="ZeroDivisionError"), needs)
    assert isinstance(quoted, Fail) and "is not in what the command printed" in quoted.reasons[0]


def test_each_example_names_a_test_that_is_in_the_tests(repo: Path, tmp_path: Path) -> None:
    needs = Witnesses(ran_by(identity=NONE), copy(repo, tmp_path, {"tests/test_mul.py": TEST}), source=repo)
    made_up = bf.fails_first(spec(examples=[{"case": "Multiplying by 0", "result": "gives 0",
                                            "test": "test_mul_by_zero"}]), needs)
    assert isinstance(made_up, Fail) and "test_mul_by_zero is not in tests/test_mul.py" in made_up.reasons[0]
    none = bf.fails_first(spec(examples=[]), needs)
    assert isinstance(none, Fail) and "examples" in none.reasons[0]
    long = bf.fails_first(spec(examples=[{"case": "Multiplying 2 by 3, " * 3, "result": "gives 6",
                                         "test": "test_mul_multiplies"}]), needs)
    assert isinstance(long, Fail) and "is 59 characters" in long.reasons[0] and "at most 50" in long.reasons[0]
    wordy = bf.fails_first(spec(examples=[{"case": "Multiplying 2 by 3", "result": "it gives six, " * 6,
                                          "test": "test_mul_multiplies"}]), needs)
    assert isinstance(wordy, Fail) and "at most 70" in wordy.reasons[0]
    huge = bf.fails_first(spec(examples=[{"case": "Multiplying 2 by 3", "result": "gives 6",
                                         "test": "test_mul_multiplies", "screen": "<p>6</p>" * 6000}]), needs)
    assert isinstance(huge, Fail) and "the screen of example 1" in huge.reasons[0]


SCREEN = "<html><body style='background:#123456'><h1>2 x 3 = 6</h1></body></html>"


def test_the_examples_stop_shows_each_example_with_its_screen_and_the_agents_summary_below() -> None:
    drawn = spec(examples=[{"case": "Multiplying 2 by 3", "result": "gives 6", "test": "test_mul_multiplies",
                            "screen": SCREEN},
                           {"case": "Multiplying 0 by 3", "result": "gives 0", "test": "test_mul_multiplies"}])
    needs = Witnesses(ran_by(specify=Checked("specify", drawn, "h", "c"), identity=NONE,
                             sketch=Checked("sketch", bf.Sketched(pictures=["example-1.png", ""]), "h", "c")))
    shown = bf.show_examples(None, needs)
    assert [(x.title, x.text, x.more, [(p.step, p.file) for p in x.pictures]) for x in shown] == [
        ("Multiplying 2 by 3", "gives 6.", False, [("sketch", "example-1.png")]),
        ("Multiplying 0 by 3", "gives 0.", False, []),
        ("What the tests cover", "mul multiplies two numbers. In tests/test_mul.py.", True, []),
        ("No identity yet", "The screens look as the agent drew them, until visual-identity runs.", True, [])]
    needs = Witnesses(ran_by(specify=Checked("specify", drawn, "h", "c"), identity=Checked("identity", IDENTITY, "h", "c"),
                             sketch=Checked("sketch", bf.Sketched(pictures=["", ""]), "h", "c")))
    assert (bf.show_examples(None, needs)[-1].title, bf.show_examples(None, needs)[-1].text) == (
        "The project's identity", "3 colours and 1 font, in static/app.css; the screens use only those.")


def test_jaynes_reads_the_identity_from_the_projects_style_files_leaving_tests_and_vendors_out(
        repo: Path, tmp_path: Path) -> None:
    project = copy(repo, tmp_path, {"static/app.css": CSS, "tests/test_look.py": "RED = '#ff0000'\n",
                                    "vendor/lib/x.css": "a { color: #abcdef }", "calc.py": "x = '#123456'\n"})
    found = bf.read_identity(StepContext("r1", "t", Witnesses({}, project), tmp_path))
    assert found == IDENTITY


def test_a_sketch_uses_only_the_identitys_colours_and_fonts_when_the_project_has_one(
        repo: Path, tmp_path: Path) -> None:
    def drawn(screen: str) -> bf.Spec:
        return spec(examples=[{"case": "Multiplying 2 by 3", "result": "gives 6", "test": "test_mul_multiplies",
                               "screen": screen}])
    with_one = Witnesses(ran_by(identity=Checked("identity", IDENTITY, "h", "c")),
                         copy(repo, tmp_path, {"tests/test_mul.py": TEST}), source=repo)
    outside = bf.fails_first(drawn("<p style='color:#FF0000; font-family: Comic Sans MS, cursive'>6</p>"), with_one)
    assert isinstance(outside, Fail)
    assert "example 1 uses #ff0000, comic sans ms" in outside.reasons[0] and "static/app.css" in outside.reasons[0]
    inside = drawn("<p style='color:#1e88e5; background: var(--bg); font-family: Inter, Arial, sans-serif'>6</p>")
    assert isinstance(bf.fails_first(inside, with_one), Pass)
    without = Witnesses(ran_by(identity=NONE), copy(repo, tmp_path / "w", {"tests/test_mul.py": TEST}), source=repo)
    assert isinstance(bf.fails_first(drawn("<p style='color:#FF0000'>6</p>"), without), Pass)


def test_the_build_may_not_bring_a_colour_or_font_the_project_did_not_have(repo: Path, tmp_path: Path) -> None:
    has = Checked("identity", IDENTITY, "h", "c")
    brought = copy(repo, tmp_path, {"static/app.css": CSS,
                                    "web/page.html": "<style>h1 { color: #ff0000; font-family: 'Comic Sans MS' }</style>"})
    refused = bf.keeps_identity(bf.Built(summary="s", files=["web/page.html"]), Witnesses(ran_by(identity=has), brought))
    assert isinstance(refused, Fail) and "web/page.html brings #ff0000, comic sans ms" in refused.reasons[0]
    kept = copy(repo, tmp_path / "k", {"static/app.css": CSS,
                                       "web/page.html": "<style>h1 { color: var(--accent); font-family: Inter }</style>"})
    assert isinstance(bf.keeps_identity(bf.Built(summary="s", files=[]), Witnesses(ran_by(identity=has), kept)), Pass)
    free = copy(repo, tmp_path / "f", {"web/page.html": "<style>h1 { color: #ff0000 }</style>"})
    assert isinstance(bf.keeps_identity(bf.Built(summary="s", files=[]), Witnesses(ran_by(identity=NONE), free)), Pass)


@pytest.mark.skipif(chrome() is None, reason="needs Google Chrome")
def test_jaynes_draws_each_screen_the_agent_described_and_nothing_else(tmp_path: Path) -> None:
    drawn = spec(examples=[{"case": "Multiplying 2 by 3", "result": "gives 6", "test": "test_mul_multiplies",
                            "screen": SCREEN + "<img src='https://example.com/x.png'>"},
                           {"case": "Multiplying 0 by 3", "result": "gives 0", "test": "test_mul_multiplies"}])
    ctx = StepContext("r1", "add mul", Witnesses(ran_by(specify=Checked("specify", drawn, "h", "c"))), tmp_path)
    out = bf.draw_sketches(ctx)
    assert out.pictures == ["example-1.png", ""]
    png = (tmp_path / "files" / "example-1.png").read_bytes()
    assert png.startswith(b"\x89PNG")
    assert sorted(p.name for p in (tmp_path / "files").iterdir()) == ["example-1.png"]


def test_the_code_must_pass_the_new_tests_and_the_whole_suite(repo: Path, tmp_path: Path) -> None:
    agreed = Checked("specify", spec(), "h", "c")
    missing = Witnesses(ran_by(specify=agreed, identity=NONE), copy(repo, tmp_path, {"tests/test_mul.py": TEST}),
                        source=repo)
    failed = bf.built(bf.Built(summary="s", files=[]), missing)
    assert isinstance(failed, Fail) and "the tests still fail" in failed.reasons[0]
    wordy = bf.built(bf.Built(summary="what changed, " * 40, files=[]), missing)
    assert isinstance(wordy, Fail) and "summary is 560 characters, at most 400" in wordy.reasons[0]


def test_the_role_asks_at_the_tests_and_the_merge_and_the_code_never_touches_the_tests(
        repo: Path, tmp_path: Path) -> None:
    assert verify(bf.ROLE) == []
    agent = Scripted(name="claude", plan={
        ("specify", 1): edit({"tests/test_mul.py": TEST}, spec().model_dump()),
        ("specify", 2): edit({"tests/test_mul.py": TEST}, spec().model_dump()),
        ("build", 1): edit({"tests/test_mul.py": "def test_mul_multiplies():\n    pass\n"},
                           {"summary": "s", "files": []}),
        ("build", 2): edit({"calc.py": MUL}, {"summary": "adds mul", "files": ["calc.py"]}),
    })
    c = Conductor(tmp_path / "home", runtimes={"claude": lambda: agent})
    first = c.run(bf.ROLE, "add mul(a, b)", run_id="r1", project=repo)
    assert (first.status, first.step) == ("waiting", "agree")
    [opened] = [e.payload for e in c.store.events("StopOpened", run_id="r1")]
    assert opened["question"] == "Do these examples show what you asked for?"
    assert [x["title"] for x in opened["shown"]] == ["Multiplying 2 by 3", "What the tests cover", "No identity yet"]

    c.answer(bf.ROLE, "r1", "agree", "return", note="show 2 by 4 too", to="specify", by="person")
    again = c.run(bf.ROLE, "add mul(a, b)", run_id="r1")
    assert (again.status, again.step) == ("waiting", "agree")
    assert "show 2 by 4 too" in agent.prompts[-1]
    c.answer(bf.ROLE, "r1", "agree", "accept", by="person")
    last = c.run(bf.ROLE, "add mul(a, b)", run_id="r1")
    assert (last.status, last.step) == ("waiting", "accept")
    merge = [e.payload for e in c.store.events("StopOpened", run_id="r1")][-1]
    assert merge["question"] == "Merge this feature?"  # the summary is shown below, never as the heading
    assert [(x["title"], x["text"], x["more"]) for x in merge["shown"]] == [
        ("What changed", "adds mul.", False), ("Files changed", "calc.py.", True)]
    [rejected] = [e.payload for e in c.store.events("TurnRejected", run_id="r1")]
    assert rejected["step"] == "build" and "tests/test_mul.py" in rejected["reasons"][0]

    c.answer(bf.ROLE, "r1", "accept", "accept", by="person")
    assert c.run(bf.ROLE, "add mul(a, b)", run_id="r1").status == "finished"
    branch = git(repo, "branch", "--list", "jaynes/*").strip(" *")
    assert git(repo, "show", f"{branch}:calc.py").endswith("a * b")
    assert git(repo, "show", f"{branch}:tests/test_mul.py") == TEST.strip()
