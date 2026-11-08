"""``jaynes run`` refuses bad input with a message instead of a traceback."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from jaynes import cli
from jaynes.conductor import Conductor
from jaynes.role import role
from jaynes.store import Store
from tests.fakes import Scripted

ROOT = Path(__file__).resolve().parents[1]


class Note(BaseModel):
    text: str


def test_runtime_override_without_equals_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="name=module:attribute"):
        cli.main(["run", "--home", str(tmp_path), "--role", "tests.durable:ROLE", "--task", "t", "--runtime", "slow"])


def test_resuming_a_run_started_from_python_is_refused_clearly(tmp_path: Path) -> None:
    r = role("py", agent="fake")
    r.step("a", output=Note)
    Conductor(tmp_path, runtimes={"fake": lambda: Scripted(default=lambda s: {"text": s})}).run(r, "t", run_id="r1")
    with pytest.raises(SystemExit, match="started from Python"):
        cli.main(["run", "--home", str(tmp_path), "r1"])


def jaynes(home: Path, *args: str) -> str:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    done = subprocess.run([sys.executable, "-m", "jaynes.cli", *args[:1], "--home", str(home), *args[1:]],
                          env=env, capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_answer_from_the_cli_continues_the_run_in_the_background(tmp_path: Path) -> None:
    started = json.loads(jaynes(tmp_path, "run", "--role", "tests.stopped:ROLE", "--task", "t",
                                "--runtime", "quick=tests.stopped:Agent"))
    assert (started["status"], started["step"]) == ("waiting", "draft")
    run = started["run"]
    assert f"{run}  stop  draft" in jaynes(tmp_path, "queue")
    assert "Ship 'draft one'?" in jaynes(tmp_path, "queue")

    jaynes(tmp_path, "answer", run, "draft", "change", "--output", json.dumps({"text": "draft two"}),
           "--note", "warmer")

    store = Store(tmp_path / "jaynes.sqlite3")
    deadline = time.monotonic() + 30
    while not list(store.events("RunFinished", run_id=run)):
        assert time.monotonic() < deadline, (tmp_path / "runs" / run / "continue.log").read_text()
        time.sleep(0.05)
    answered = [e.payload for e in store.events("StopAnswered", run_id=run)]
    assert [(a["answer"], a["label"], a["note"]) for a in answered] == [("change", "error", "warmer")]
    assert jaynes(tmp_path, "queue").strip() == "nothing is waiting"


def test_an_answer_from_the_cli_is_an_agent_and_can_be_bound_to_what_the_queue_showed(tmp_path: Path) -> None:
    run = json.loads(jaynes(tmp_path, "run", "--role", "tests.stopped:ROLE", "--task", "t",
                            "--runtime", "quick=tests.stopped:Agent"))["run"]
    queue = jaynes(tmp_path, "queue")
    [evidence] = [line.split("evidence ")[1].strip() for line in queue.splitlines() if "evidence " in line]
    with pytest.raises(SystemExit, match="changed since"):
        cli.main(["answer", "--home", str(tmp_path), run, "draft", "accept", "--evidence", "0" * 16])

    jaynes(tmp_path, "answer", run, "draft", "accept", "--evidence", evidence)

    [answered] = [e.payload for e in Store(tmp_path / "jaynes.sqlite3").events("StopAnswered", run_id=run)]
    assert answered["by"] == "agent" and answered["label"] == "correct"


def test_a_built_in_role_is_started_by_its_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["run", "--home", str(tmp_path), "--role", "visual-identity", "--task", "t"])
    assert json.loads(capsys.readouterr().out)["step"] == "current"  # it ran: the first step wants a project
    [started] = [e.payload for e in Store(tmp_path / "jaynes.sqlite3").events("RunStarted")]
    assert started["ref"] == "jaynes.roles.visual_identity:ROLE"


def test_an_unknown_role_name_lists_the_built_in_ones(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="visual-identity, redesign"):
        cli.main(["run", "--home", str(tmp_path), "--role", "landing-page", "--task", "t"])


def test_an_answer_while_a_process_holds_the_run_is_carried_on_once_it_lets_go(tmp_path: Path) -> None:
    run = json.loads(jaynes(tmp_path, "run", "--role", "tests.stopped:ROLE", "--task", "t",
                            "--runtime", "quick=tests.stopped:Agent"))["run"]
    c = Conductor(tmp_path)
    store = Store(tmp_path / "jaynes.sqlite3")
    with c._lock(run):  # pyright: ignore[reportPrivateUsage]
        jaynes(tmp_path, "answer", run, "draft", "accept")  # recorded at once; the run goes on in the background
        time.sleep(1.5)
        assert list(store.events("RunFinished", run_id=run)) == []  # it waits for the process holding the run
    deadline = time.monotonic() + 30
    while not list(store.events("RunFinished", run_id=run)):
        assert time.monotonic() < deadline, (tmp_path / "runs" / run / "continue.log").read_text()
        time.sleep(0.05)
    assert "going on once it lets go" in (tmp_path / "runs" / run / "continue.log").read_text()


def test_an_agent_finds_a_delivery_and_records_its_correction(tmp_path: Path,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    from tests.stopped import ROLE
    home = str(tmp_path)
    cli.main(["run", "--home", home, "--role", "tests.stopped:ROLE", "--task", "Ship the draft",
              "--runtime", "quick=tests.stopped:Agent"])
    run = json.loads(capsys.readouterr().out)["run"]
    with pytest.raises(SystemExit, match="return it from a stop"):
        cli.main(["correct", "--home", home, run, "--step", "draft", "--kind", "error", "--note", "too long"])
    Conductor(tmp_path).answer(ROLE, run, "draft", "accept", by="person")
    cli.main(["run", "--home", home, run])
    assert json.loads(capsys.readouterr().out)["status"] == "finished"

    cli.main(["delivered", "--home", home])
    listed = capsys.readouterr().out
    assert f"{run}  delivered " in listed and "stopped: Ship the draft" in listed
    assert "    steps: draft (stop), final" in listed

    cli.main(["correct", "--home", home, run, "--step", "draft", "--kind", "error", "--note", "too long"])
    recorded = json.loads(capsys.readouterr().out)
    assert (recorded["kind"], recorded["counted_at"]) == ("error", "draft")
    assert recorded["page"].endswith(f"/run/{run}")
    cli.main(["delivered", "--home", home])
    assert "    corrected: error in draft, counted at draft: too long" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="no step 'ship'"):
        cli.main(["correct", "--home", home, run, "--step", "ship", "--kind", "error", "--note", "x"])
