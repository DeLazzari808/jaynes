"""The home page in four parts: what holds a run waits for the person, the samples a run went past, what was
delivered since their last visit, and what runs now."""

from __future__ import annotations

import importlib
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from jaynes import cli
from jaynes.conductor import Conductor
from jaynes.review import VISIT_GAP, visit
from jaynes.store import Store
from tests.test_review import get, serving


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "start"]):
        git(r, *args)
    return r


def run(home: Path, *args: str) -> str:
    before = set(Store(home / "jaynes.sqlite3").runs()) if (home / "jaynes.sqlite3").exists() else set[str]()
    cli.main(["run", "--home", str(home), *args])
    [new] = set(Store(home / "jaynes.sqlite3").runs()) - before
    return new


def held(home: Path, task: str) -> str:
    """A run that waits at its stop: the step after it needs the answer."""
    return run(home, "--role", "tests.held:ROLE", "--task", task, "--runtime", "quick=tests.held:Agent")


def stopped(home: Path, task: str) -> str:
    """A run whose every step ran: its open stop only lets it finish."""
    return run(home, "--role", "tests.stopped:ROLE", "--task", task, "--runtime", "quick=tests.stopped:Agent")


def steps(c: Conductor) -> Callable[[str], list[str] | None]:
    def of(run_id: str) -> list[str] | None:
        module, _, attr = str(next(iter(c.store.events("RunStarted", run_id=run_id))).payload["ref"]).partition(":")
        return [s.name for s in getattr(importlib.import_module(module), attr).steps]
    return of


def merged(home: Path, tmp_path: Path) -> str:
    """A run that merged by itself once its hidden case passed, with its last stop left open as a sample."""
    return run(home, "--role", "tests.merging:ROLE", "--task", "add should add", "--runtime",
               "quick=tests.merging:Agent", "--project", str(repo(tmp_path)))


def parts(page: str) -> dict[str, str]:
    """Each part of the home page, by its heading."""
    main = page.partition('<div class="body home">')[2]
    out: dict[str, str] = {}
    for chunk in main.split('<section class="part">')[1:]:
        title = chunk.partition("<h2>")[2].partition("</h2>")[0]
        out[title] = chunk
    return out


def test_a_stop_holds_its_run_while_a_step_left_needs_it_and_any_other_is_a_sample(tmp_path: Path) -> None:
    home = tmp_path / "home"
    waiting = held(home, "write the draft")  # nothing runs it and its last step needs the answer
    went_on = merged(home, tmp_path)  # merged: its open stop only lets it finish
    ran = stopped(home, "write it all")  # every step ran: the same
    working = held(home, "write another")
    c = Conductor(home)
    with c._lock(working):  # pyright: ignore[reportPrivateUsage]  # a process runs it: it goes on past its stop
        holds = {w.run_id: w.holds for w in c.queue(idle_after=0, steps=steps(c)) if w.kind == "stop"}
        assert [w.run_id for w in c.queue(idle_after=0) if w.kind == "running"] == [working]
    assert holds == {waiting: True, went_on: False, ran: False, working: False}
    assert [w.run_id for w in c.queue(idle_after=0, steps=steps(c)) if w.holding] == [waiting, working]
    # without the roles, what has not delivered a branch holds: better asked than missed
    assert [w.run_id for w in c.queue(idle_after=0) if w.holding] == [waiting, ran, working]


def test_the_home_page_shows_what_waits_the_samples_what_was_delivered_and_what_runs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    waiting = held(home, "write the draft")
    went_on = merged(home, tmp_path)
    working = held(home, "write another")
    base, server = serving(home)
    try:
        with Conductor(home)._lock(working):  # pyright: ignore[reportPrivateUsage]
            page = get(base + "/")
    finally:
        server.shutdown()
    p = parts(page)
    assert list(p) == ["Waiting for you", "To look at", "Delivered", "Running now"]
    assert f'href="/stop/{waiting}/agree"' in p["Waiting for you"]
    assert "Is the draft what you asked?" in p["Waiting for you"]
    assert went_on not in p["Waiting for you"] and working not in p["Waiting for you"]
    assert f'href="/stop/{went_on}/accept"' in p["To look at"] and "Merge this?" in p["To look at"]
    assert f'href="/stop/{working}/agree"' in p["To look at"]  # it runs on past its stop
    delivered = p["Delivered"]
    assert f'href="/run/{went_on}"' in delivered and "into main by itself" in delivered
    assert "1 of 1 hidden checks passed" in delivered and "1 sample to look at" in delivered
    assert "In the last 7 days: this is your first visit." in delivered and waiting not in delivered
    assert f'href="/run/{working}"' in p["Running now"] and "running" in p["Running now"]
    assert '<div class="qhead" data-waiting="1">' in page  # the tab's count and its notification: what holds alone


def test_what_was_delivered_before_the_last_visit_folds_under_earlier(tmp_path: Path) -> None:
    home = tmp_path / "home"
    went_on = merged(home, tmp_path)
    now = time.time()
    (home / "visits.json").write_text(json.dumps({"last": now, "since": now}))  # a visit that began after the merge
    base, server = serving(home)
    try:
        delivered = parts(get(base + "/"))["Delivered"]
    finally:
        server.shutdown()
    new, _, earlier = delivered.partition("<summary>Earlier")
    assert "Nothing new was delivered." in new and f'href="/run/{went_on}"' in earlier


def test_a_visit_lasts_while_the_person_looks_again_within_the_gap(tmp_path: Path) -> None:
    assert visit(tmp_path, 100.0) is None  # the first visit
    assert visit(tmp_path, 100.0 + VISIT_GAP) is None  # the same visit, the page reloading itself
    later = 100.0 + 3 * VISIT_GAP
    assert visit(tmp_path, later) == 100.0 + VISIT_GAP  # a new visit: since the last look of the one before
    assert visit(tmp_path, later + 60) == 100.0 + VISIT_GAP
    assert visit(tmp_path, later + 10 * VISIT_GAP, record=False) == later + 60  # an agent looking records nothing
    assert json.loads((tmp_path / "visits.json").read_text())["last"] == later + 60


def test_next_opens_what_holds_first_then_a_sample_then_the_home_page(tmp_path: Path) -> None:
    home = tmp_path / "home"
    went_on = merged(home, tmp_path)
    base, server = serving(home)
    try:
        assert "Merge this?" in get(base + "/next") and f'value="{went_on}"' in get(base + "/next")
        waiting = held(home, "write the draft")
        assert "Is the draft what you asked?" in get(base + "/next") and f'value="{waiting}"' in get(base + "/next")
    finally:
        server.shutdown()
    empty = tmp_path / "empty"
    base, server = serving(empty)
    try:
        assert "<h2>Waiting for you</h2>" in get(base + "/next") and "Nothing holds a run." in get(base + "/next")
    finally:
        server.shutdown()
