"""The built-in small-change role, the light path: the change and the suite, and a person only when the change is
bigger than a small one or touches a path that runs later."""

from __future__ import annotations

from pathlib import Path

from jaynes.conductor import Conductor
from jaynes.role import Checked, Fail, Pass, Witnesses
from jaynes.roles import small_change as sc
from jaynes.verify import verify
from tests.fakes import Scripted
from tests.test_fix_bug import PYTHON, copy, edit, git, pytestmark, python, repo  # noqa: F401  # pyright: ignore[reportUnusedImport]

FIXED = "def add(a, b):\n    return a + b\n"


def stops(c: Conductor, run: str) -> list[str]:
    return [e.type for e in c.store.events(run_id=run) if e.type in ("StopOpened", "StopSkipped")]


def test_a_small_change_that_passes_the_suite_goes_through_without_a_stop(repo: Path, tmp_path: Path) -> None:
    assert verify(sc.ROLE) == []
    agent = Scripted(name="claude", plan={("change", 1): edit({"calc.py": FIXED}, {"summary": "adds",
                                                                                  "files": ["calc.py"]})})
    c = Conductor(tmp_path / "home", runtimes={"claude": lambda: agent})
    assert c.run(sc.ROLE, "add should add", run_id="r1", project=repo).status == "finished"
    assert stops(c, "r1") == ["StopSkipped"]
    assert git(repo, "show", "jaynes/r1:calc.py").endswith("a + b")


def test_a_change_bigger_than_a_small_one_stops_for_a_person(repo: Path, tmp_path: Path) -> None:
    big = FIXED + "".join(f"\n\ndef f{i}():\n    return {i}\n" for i in range(12))
    agent = Scripted(name="claude", plan={("change", 1): edit({"calc.py": big}, {"summary": "adds",
                                                                                "files": ["calc.py"]})})
    c = Conductor(tmp_path / "home", runtimes={"claude": lambda: agent})
    waiting = c.run(sc.ROLE, "add should add", run_id="r1", project=repo)
    assert (waiting.status, waiting.step) == ("waiting", "change")
    [opened] = [e.payload for e in c.store.events("StopOpened", run_id="r1")]
    assert opened["status"] == "floor" and opened["question"].startswith("This change is bigger than a small one")


def test_the_suite_must_pass_and_a_project_without_tests_needs_none(repo: Path, tmp_path: Path) -> None:
    broken = Witnesses({"runner": Checked("runner", sc.Suite(command=PYTHON), "h", "c")},
                       copy(repo, tmp_path, {"tests/test_zero.py": "def test_zero():\n    assert False\n"}),
                       source=repo)
    failed = sc.passes(sc.Changed(summary="s", files=[]), broken)
    assert isinstance(failed, Fail) and "the suite fails" in failed.reasons[0]
    none = Witnesses({"runner": Checked("runner", sc.Suite(command=""), "h", "c")}, repo, source=repo)
    assert isinstance(sc.passes(sc.Changed(summary="s", files=[]), none), Pass)
    assert sc.lines("+++ b/x\n--- a/x\n+a\n-b\n c\n\n+d\n") == 3
