"""Merging alone: a merge commit made apart from any checkout, landed only where no uncommitted work is touched, a
correction merged on top of what was merged, and an undo that reverts it all."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from jaynes import project as repo
from jaynes import proof
from jaynes.conductor import AnswerRejected, Conductor, RunResult
from jaynes.role import Back, role, stop
from tests.fakes import Scripted, write
from tests.test_fix_bug import edit

ADDS = f"{sys.executable} -c 'from calc import add; assert add(2, 2) == 4'"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def main(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (r / "notes.md").write_text("notes\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "start")
    return r


def side(r: Path, name: str, files: dict[str, str], start: str = "main") -> str:
    """A branch off ``start`` with one commit, made without leaving the checkout."""
    git(r, "branch", name, start)
    wt = r.parent / f"wt-{name}"
    git(r, "worktree", "add", "-q", str(wt), name)
    for path, text in files.items():
        (wt / path).write_text(text)
    git(wt, "commit", "-qam", name)
    head = git(wt, "rev-parse", "HEAD")
    git(r, "worktree", "remove", "--force", str(wt))
    return head


def test_a_merge_lands_on_a_clean_checkout_and_never_on_uncommitted_work(main: Path) -> None:
    fix = side(main, "fix", {"calc.py": "def add(a, b):\n    return a + b\n"})
    (main / "notes.md").write_text("half written\n")

    with pytest.raises(repo.NotMerged, match="has changes not committed"):
        repo.merge(main, "main", fix, "Merge fix")
    assert (main / "notes.md").read_text() == "half written\n" and "a - b" in (main / "calc.py").read_text()

    git(main, "checkout", "-q", "notes.md")
    merged = repo.merge(main, "main", fix, "Merge fix")
    assert git(main, "rev-parse", "HEAD") == merged and "a + b" in (main / "calc.py").read_text()
    assert git(main, "log", "-1", "--format=%P").split()[1] == fix  # a merge commit, which one revert undoes


def test_a_branch_not_checked_out_moves_without_touching_the_checkout(main: Path) -> None:
    git(main, "checkout", "-q", "-b", "elsewhere")
    fix = side(main, "fix", {"calc.py": "def add(a, b):\n    return a + b\n"})
    (main / "notes.md").write_text("mine\n")

    merged = repo.merge(main, "main", fix, "Merge fix")

    assert git(main, "rev-parse", "main") == merged
    assert (main / "notes.md").read_text() == "mine\n" and "a - b" in (main / "calc.py").read_text()


def test_a_conflict_does_not_merge(main: Path) -> None:
    fix = side(main, "fix", {"calc.py": "def add(a, b):\n    return a + b\n"})
    (main / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    git(main, "commit", "-qam", "someone else")
    with pytest.raises(repo.NotMerged, match="conflicts"):
        repo.merge(main, "main", fix, "Merge fix")


def test_a_correction_merges_only_what_changed_and_an_undo_reverts_both(main: Path) -> None:
    first = side(main, "first", {"calc.py": "def add(a, b):\n    return a + b\n"})
    repo.merge(main, "main", first, "Merge first")
    (main / "notes.md").write_text("notes\nmore\n")
    git(main, "commit", "-qam", "the person goes on")
    again = side(main, "again", {"calc.py": "def add(a, b):\n    return a + b  # adds\n"}, start="main~1^1")

    corrected = repo.merge(main, "main", again, "Merge again", base=first)

    assert (main / "calc.py").read_text() == "def add(a, b):\n    return a + b  # adds\n"
    assert (main / "notes.md").read_text() == "notes\nmore\n"
    repo.revert(main, "main", corrected, "Revert again")
    repo.revert(main, "main", git(main, "rev-parse", "main~3"), "Revert first")
    assert "a - b" in (main / "calc.py").read_text() and (main / "notes.md").read_text() == "notes\nmore\n"


class Made(BaseModel):
    summary: str


def merging_role(merges: bool = True) -> Any:
    r = role("merging", agent="fake", merges=merges)
    cases = r.step("cases", output=proof.Cases, project="read", hidden=True)
    make = r.step("make", output=Made, project="write", may_change=["**"])
    proved = r.step("prove", output=proof.Proved, needs=[cases, make], run_by=proof.prove, project="read",
                    effect="read", max_turns=1, back=Back(make, proof.failed, rounds=1))
    r.step("accept", needs=[proved, make], stop=stop(0.05, sampled=False))
    return r


def adds(n: int) -> Any:
    return edit({"calc.py": f"def add(a, b):\n    return a + b{'  # v' + str(n) if n > 1 else ''}\n"},
                {"summary": f"adds v{n}"})


def agent() -> Scripted:
    return Scripted({("cases", 1): write({"cases": [{"says": "Adding 2 and 2 gives 4", "run": ADDS}]}),
                     ("make", 1): adds(1), ("make", 2): adds(2)})


def test_a_run_whose_proof_passes_merges_alone_before_the_answer(main: Path, tmp_path: Path) -> None:
    r = merging_role()
    maker = agent()
    c = Conductor(tmp_path / "home", runtimes={"fake": lambda: maker})

    assert c.run(r, "add should add", "r1", project=main) == RunResult("waiting", "accept")

    [merged] = [e.payload for e in c.store.events("Merged", run_id="r1")]
    assert merged["into"] == "main" and git(main, "rev-parse", "main") == merged["commit"]
    assert "a + b" in (main / "calc.py").read_text()
    c.answer(r, "r1", "accept", "return", note="say what it does", to="make", by="person")
    assert c.run(r, "add should add", "r1") == RunResult("waiting", "accept")
    again = [e.payload for e in c.store.events("Merged", run_id="r1")]
    assert len(again) == 2 and again[1]["over"] == merged["from"]  # the correction merged on top
    assert (main / "calc.py").read_text() == "def add(a, b):\n    return a + b  # v2\n"
    c.answer(r, "r1", "accept", "accept", by="person")
    assert c.run(r, "add should add", "r1").status == "finished"

    with pytest.raises(AnswerRejected, match="only a person"):
        c.undo("r1", "wrong idea", by="agent")
    undone = c.undo("r1", "wrong idea", by="person")
    assert len(undone["commits"]) == 2 and "a - b" in (main / "calc.py").read_text()
    with pytest.raises(AnswerRejected, match="already undone"):
        c.undo("r1", "again", by="person")


def test_a_failed_proof_or_dirty_checkout_does_not_merge_and_says_why_once(main: Path, tmp_path: Path) -> None:
    r = merging_role()
    wrong = agent()
    wrong.plan.update({("make", n): edit({"calc.py": f"def add(a, b):\n    return a * {n + 2}\n"}, {"summary": "no"})
                       for n in (1, 2)})
    c = Conductor(tmp_path / "home", runtimes={"fake": lambda: wrong})
    assert c.run(r, "add should add", "r1", project=main) == RunResult("waiting", "accept")
    assert [e.payload["reason"] for e in c.store.events("MergeSkipped", run_id="r1")] == [
        "not every hidden check passed"]
    assert not list(c.store.events("Merged", run_id="r1")) and "a - b" in (main / "calc.py").read_text()

    (main / "notes.md").write_text("half written\n")
    c2 = Conductor(tmp_path / "home2", runtimes={"fake": lambda: agent()})
    assert c2.run(r, "add should add", "r2", project=main).status == "waiting"
    [skipped] = [e.payload for e in c2.store.events("MergeSkipped", run_id="r2")]
    assert "has changes not committed" in skipped["reason"]
    assert c2.run(r, "add should add", "r2").status == "waiting"
    assert len(list(c2.store.events("MergeSkipped", run_id="r2"))) == 1  # said once
    git(main, "checkout", "-q", "notes.md")
    c2.answer(r, "r2", "accept", "accept", by="person")
    c2.run(r, "add should add", "r2")
    assert [e.payload["into"] for e in c2.store.events("Merged", run_id="r2")] == ["main"]  # tried again


def test_a_role_that_does_not_merge_never_does(main: Path, tmp_path: Path) -> None:
    c = Conductor(tmp_path / "home", runtimes={"fake": lambda: agent()})
    assert c.run(merging_role(merges=False), "add should add", "r1", project=main).status == "waiting"
    assert not list(c.store.events("Merged")) and not list(c.store.events("MergeSkipped"))
