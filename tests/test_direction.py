"""The person's own roles live in a folder outside Jaynes, and a role can plan a request: once its run delivered the
plan, Jaynes starts it by itself."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jaynes import catalog, cli, plan
from jaynes.conductor import Conductor, project
from tests.test_merge import git
from tests.test_plan import drain, pending, repo  # noqa: F401  # pyright: ignore[reportUnusedImport]
from tests.test_review import get, serving

RUNTIME = ["--runtime", "planner=tests.planned:Agent"]
REQUEST = "A dark look, then the home screen in it"


@pytest.fixture
def mine(tmp_path: Path) -> Path:
    """A folder of the person's roles: the planned ones, and a small-change of theirs that takes the built-in's
    place."""
    folder = tmp_path / "my-roles"
    folder.mkdir()
    (folder / "team.py").write_text("from tests.planned import DIRECTION, LOOK, SCREEN\n")
    (folder / "small.toml").write_text('name = "small-change"\nagent = "planner"\nabout = "Mine."\n\n'
                                       '[[step]]\nname = "write"\noutput = "tests.planned:Note"\n')
    (folder / "broken.py").write_text("raise RuntimeError('half written')\n")  # left out, never in the way
    return folder


def listed(capsys: pytest.CaptureFixture[str], *args: str) -> list[dict[str, Any]]:
    cli.main(["roles", *args])
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def test_a_folder_of_the_persons_roles_comes_first_and_takes_a_built_in_roles_place(
        tmp_path: Path, mine: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = str(tmp_path / "home")
    before = {r["name"]: r for r in listed(capsys, "--home", home)}
    assert before["small-change"]["from"] == "jaynes" and "direction" not in before

    roles = listed(capsys, "add", str(mine), "--home", home)

    names = [r["name"] for r in roles]
    assert names[:4] == ["small-change", "direction", "look", "screen"]  # theirs first, file by file
    assert {"fix-bug", "build-feature", "screen-design"} <= set(names) and names.count("small-change") == 1
    by = {r["name"]: r for r in roles}
    assert by["small-change"] == {"name": "small-change", "about": "Mine.", "from": str(mine), "plans": False,
                                  "ref": str(mine / "small.toml")}
    assert by["direction"]["plans"] and by["direction"]["ref"] == f"{mine / 'team.py'}:DIRECTION"
    assert by["fix-bug"]["about"].startswith("Fixes a bug")
    assert catalog.folders(tmp_path / "home") == [mine]

    cli.main(["roles", "remove", str(mine), "--home", home])
    assert "direction" not in {r["name"] for r in listed(capsys, "--home", home)}


def test_a_run_started_by_the_persons_role_name_records_where_it_lives(
        tmp_path: Path, mine: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    catalog.add(home, mine)
    monkeypatch.chdir(tmp_path)  # another folder: the run finds the role by its path, not by where it started
    cli.main(["run", "--home", str(home), "--role", "small-change", "--task", "note it", *RUNTIME])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    started = project(Conductor(home).store.events(run_id=out["run"])).started
    assert started is not None and started["ref"] == str(mine / "small.toml")
    with pytest.raises(SystemExit, match="no role 'nope': use one of small-change, direction"):
        cli.main(["run", "--home", str(home), "--role", "nope", "--task", "t"])


def test_a_role_that_plans_starts_its_plan_once_its_run_delivered(
        tmp_path: Path, repo: Path, mine: Path, pending: list[list[str]],  # noqa: F811
        capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    catalog.add(home, mine)

    cli.main(["run", "--home", str(home), "--role", "direction", "--task", REQUEST, "--project", str(repo),
              *RUNTIME])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    c = Conductor(home)
    st = project(c.store.events(run_id=out["run"]))
    planned = st.step("plan").checked
    assert planned is not None and planned["attempt"] == 2  # the first plan named a role nobody has
    said = json.loads((c.home / "objects" / f"{planned['output_hash']}.json").read_text())
    assert said["why"].startswith("roles shown: small-change, look, screen, visual-identity")  # never itself
    assert "direction" not in said["why"]
    assert out["status"] == "waiting" and out["stops"] == 1  # its sample waits; the plan does not
    req = out["request"]
    assert [(r["name"], r["state"]) for r in req["runs"]] == [("look", "working"), ("screen", "planned")]

    [made] = plan.requests(c)
    assert made.text == REQUEST and made.by == out["run"] and made.project == str(repo)
    assert [p.role for p in made.runs] == [f"{mine / 'team.py'}:LOOK", f"{mine / 'team.py'}:SCREEN"]
    look, screen = drain(pending, capsys)
    second = project(c.store.events(run_id=screen))
    assert second.started is not None
    assert second.started["project"]["base"] == project(c.store.events(run_id=look)).branch["commit"]  # type: ignore[index]
    assert plan.status(c, made) == "delivered"
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "main")  # the plan read the project only


def test_a_plan_that_names_a_role_nobody_has_is_sent_back_with_why(
        tmp_path: Path, repo: Path, mine: Path, pending: list[list[str]],  # noqa: F811
        capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    catalog.add(home, mine)
    cli.main(["run", "--home", str(home), "--role", "direction", "--task", REQUEST, "--project", str(repo),
              *RUNTIME])
    run = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["run"]

    events = list(Conductor(home).store.events(run_id=run))
    rejected = [e.payload for e in events if e.type == "TurnRejected"]
    assert any("run look: no role 'painter'" in r for e in rejected for r in e.get("reasons", [])), rejected


def test_the_page_starts_a_plan_whose_run_delivered_and_died(
        tmp_path: Path, repo: Path, mine: Path, pending: list[list[str]],  # noqa: F811
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    catalog.add(home, mine)
    def dies(c: Conductor, run_id: str) -> None:  # it delivers, and its process dies
        pass

    with monkeypatch.context() as m:
        m.setattr(cli, "_next_in_plan", dies)
        cli.main(["run", "--home", str(home), "--role", "direction", "--task", REQUEST, "--project", str(repo),
                  *RUNTIME])
    run = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["run"]
    c = Conductor(home)
    assert plan.requests(c) == []

    plan.advance_all(c)
    plan.advance_all(c)

    [made] = plan.requests(c)
    assert made.by == run and [a[1] for a in pending] == [made.runs[0].run]  # recorded and started once


def test_the_page_ties_the_plan_to_the_run_that_made_it(
        tmp_path: Path, repo: Path, mine: Path, pending: list[list[str]],  # noqa: F811
        capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    catalog.add(home, mine)
    cli.main(["run", "--home", str(home), "--role", "direction", "--task", REQUEST, "--project", str(repo),
              *RUNTIME])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    base, server = serving(home)
    try:
        req = get(f"{base}/request/{out['request']['request']}")
        assert f'Planned by <a href="/run/{out["run"]}">run {out["run"]}</a>' in req
        run = get(f"{base}/run/{out['run']}")
        assert f'This run planned <a href="/request/{out["request"]["request"]}">' in run and "2 of them" in run
        sample = get(f"{base}/stop/{out['run']}/plan")
        assert f'Jaynes started <a href="/request/{out["request"]["request"]}">this plan</a>' in sample
        assert "only what used it runs again" not in sample
    finally:
        server.shutdown()
