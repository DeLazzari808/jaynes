"""The built-in build-feature role: tests of the agreed plan that fail first, then code that makes them and the suite
pass, with a person at the tests and at the merge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jaynes.conductor import Conductor
from jaynes.role import Checked, Fail, Pass, Witnesses
from jaynes.roles import build_feature as bf
from jaynes.verify import verify
from tests.fakes import Scripted
from tests.test_fix_bug import PYTHON, copy, edit, git, pytestmark, python, ran_by, repo  # noqa: F401  # pyright: ignore[reportUnusedImport]

TEST = "from calc import mul\n\n\ndef test_mul_multiplies():\n    assert mul(2, 3) == 6\n"
MUL = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"


def spec(**overrides: Any) -> bf.Spec:
    base: dict[str, Any] = {"feature": "mul multiplies two numbers", "tests": ["tests/test_mul.py"],
                            "failure": "cannot import name 'mul'",
                            "examples": [{"case": "Multiplying 2 by 3", "result": "gives 6",
                                          "test": "test_mul_multiplies"}]}
    return bf.Spec(**{**base, **overrides})


def test_the_tests_must_fail_before_the_feature_exists(repo: Path, tmp_path: Path) -> None:
    needs = Witnesses(ran_by(), copy(repo, tmp_path, {"tests/test_mul.py": TEST}), source=repo)
    assert isinstance(bf.fails_first(spec(), needs), Pass)
    passing = bf.fails_first(spec(tests=["tests/test_zero.py"]), needs)
    assert isinstance(passing, Fail) and "the tests pass before the feature exists" in passing.reasons[0]
    elsewhere = bf.fails_first(spec(tests=["calc.py"]), needs)
    assert isinstance(elsewhere, Fail) and "calc.py is not a test file" in elsewhere.reasons[0]
    quoted = bf.fails_first(spec(failure="ZeroDivisionError"), needs)
    assert isinstance(quoted, Fail) and "is not in what the command printed" in quoted.reasons[0]


def test_each_example_names_a_test_that_is_in_the_tests(repo: Path, tmp_path: Path) -> None:
    needs = Witnesses(ran_by(), copy(repo, tmp_path, {"tests/test_mul.py": TEST}), source=repo)
    made_up = bf.fails_first(spec(examples=[{"case": "Multiplying by 0", "result": "gives 0",
                                            "test": "test_mul_by_zero"}]), needs)
    assert isinstance(made_up, Fail) and "test_mul_by_zero is not in tests/test_mul.py" in made_up.reasons[0]
    none = bf.fails_first(spec(examples=[]), needs)
    assert isinstance(none, Fail) and "examples" in none.reasons[0]


def test_the_tests_stop_shows_the_examples_in_front_and_the_agents_summary_below() -> None:
    shown = bf.show_examples(spec(), Witnesses({}))
    assert [(x.title, x.text, x.more) for x in shown] == [
        ("Multiplying 2 by 3", "gives 6.", False),
        ("What the tests cover", "mul multiplies two numbers. In tests/test_mul.py.", True)]


def test_the_code_must_pass_the_new_tests_and_the_whole_suite(repo: Path, tmp_path: Path) -> None:
    agreed = Checked("specify", spec(), "h", "c")
    missing = Witnesses(ran_by(specify=agreed), copy(repo, tmp_path, {"tests/test_mul.py": TEST}), source=repo)
    failed = bf.built(bf.Built(summary="s", files=[]), missing)
    assert isinstance(failed, Fail) and "the tests still fail" in failed.reasons[0]


def test_the_role_asks_at_the_tests_and_the_merge_and_the_code_never_touches_the_tests(
        repo: Path, tmp_path: Path) -> None:
    assert verify(bf.ROLE) == []
    agent = Scripted(name="claude", plan={
        ("specify", 1): edit({"tests/test_mul.py": TEST}, spec().model_dump()),
        ("build", 1): edit({"tests/test_mul.py": "def test_mul_multiplies():\n    pass\n"},
                           {"summary": "s", "files": []}),
        ("build", 2): edit({"calc.py": MUL}, {"summary": "adds mul", "files": ["calc.py"]}),
    })
    c = Conductor(tmp_path / "home", runtimes={"claude": lambda: agent})
    first = c.run(bf.ROLE, "add mul(a, b)", run_id="r1", project=repo)
    assert (first.status, first.step) == ("waiting", "specify")
    [opened] = [e.payload for e in c.store.events("StopOpened", run_id="r1")]
    assert opened["question"] == "Do these examples show what you asked for?"

    c.answer(bf.ROLE, "r1", "specify", "accept", by="person")
    last = c.run(bf.ROLE, "add mul(a, b)", run_id="r1")
    assert (last.status, last.step) == ("waiting", "accept")
    [rejected] = [e.payload for e in c.store.events("TurnRejected", run_id="r1")]
    assert rejected["step"] == "build" and "tests/test_mul.py" in rejected["reasons"][0]

    c.answer(bf.ROLE, "r1", "accept", "accept", by="person")
    assert c.run(bf.ROLE, "add mul(a, b)", run_id="r1").status == "finished"
    branch = git(repo, "branch", "--list", "jaynes/*").strip(" *")
    assert git(repo, "show", f"{branch}:calc.py").endswith("a * b")
    assert git(repo, "show", f"{branch}:tests/test_mul.py") == TEST.strip()
