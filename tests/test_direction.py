"""Every set of roles is a package the projects that list it use, as npm libraries; a role can plan a request, and
once its run delivered the plan, Jaynes starts it by itself."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from jaynes import cli, packages, plan
from jaynes.conductor import Conductor, project
from tests.test_merge import git
from tests.test_plan import drain, pending, repo  # noqa: F401  # pyright: ignore[reportUnusedImport]
from tests.test_api import call, mcp, tool
from tests.test_review import form, get, post, serving

RUNTIME = ["--runtime", "planner=tests.planned:Agent"]
REQUEST = "A dark look, then the home screen in it"
MANIFEST = 'name = "team"\nversion = "0.1.0"\nabout = "A team that plans."\napi = 1\n'


@pytest.fixture
def mine(tmp_path: Path) -> Path:
    """A package of the person's: the planned roles, and a small-change of theirs that takes the place of the one
    that comes with Jaynes."""
    folder = tmp_path / "my-roles"
    folder.mkdir()
    (folder / packages.MANIFEST).write_text(MANIFEST)
    (folder / "team.py").write_text("from tests.planned import DIRECTION, LOOK, SCREEN\n")
    (folder / "small.toml").write_text('name = "small-change"\nagent = "planner"\nabout = "Mine."\n\n'
                                       '[[step]]\nname = "write"\noutput = "tests.planned:Note"\n')
    (folder / "broken.py").write_text("raise RuntimeError('half written')\n")  # left out, never in the way
    return folder


def listed(capsys: pytest.CaptureFixture[str], *args: str) -> list[dict[str, Any]]:
    cli.main(list(args))
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def test_a_package_linked_for_one_project_comes_first_there_and_nowhere_else(
        tmp_path: Path, repo: Path, mine: Path, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    home = str(tmp_path / "home")
    before = {r["name"]: r for r in listed(capsys, "roles", "--project", str(repo), "--home", home)}
    assert before["small-change"]["package"] == "software" and "direction" not in before
    assert set(before) == {"build-feature", "fix-bug", "small-change", "redesign", "screen-design", "visual-identity"}

    cli.main(["add", str(mine), "--project", str(repo), "--home", home])
    assert "team 0.1.0 linked for" in capsys.readouterr().out

    roles = listed(capsys, "roles", "--project", str(repo), "--home", home)
    names = [r["name"] for r in roles]
    assert names[:4] == ["small-change", "direction", "look", "screen"]  # the package added last comes first
    assert names.count("small-change") == 1
    by = {r["name"]: r for r in roles}
    assert by["small-change"] == {"name": "small-change", "about": "Mine.", "package": "team", "plans": False,
                                  "ref": str(mine / "small.toml")}
    assert by["direction"]["plans"] and by["direction"]["ref"] == f"{mine / 'team.py'}:DIRECTION"
    assert by["fix-bug"]["about"].startswith("Fixes a bug")
    assert "direction" not in {r["name"] for r in listed(capsys, "roles", "--project", str(tmp_path), "--home", home)}
    [team] = [p for p in listed(capsys, "packages", "--home", home) if p["name"] == "team"]
    assert team["linked"] and team["projects"] == [str(repo)] and not team["default"]

    cli.main(["remove", "team", "--project", str(repo), "--home", home])
    assert "direction" not in {r["name"] for r in listed(capsys, "roles", "--project", str(repo), "--home", home)}


def test_a_package_from_git_is_installed_at_a_version_a_run_keeps_and_update_moves(
        tmp_path: Path, mine: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "team-repo"
    source.mkdir()
    (source / packages.MANIFEST).write_text(MANIFEST)
    (source / "small.toml").write_text((mine / "small.toml").read_text())
    git(source, "init", "-q", "-b", "main")
    git(source, "add", "-A")
    git(source, "commit", "-qm", "first")
    home = tmp_path / "home"

    first = packages.add(home, f"file://{source}")  # from git, as from any address git clones
    cli.main(["run", "--home", str(home), "--role", "small-change", "--task", "note it", *RUNTIME])
    run = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["run"]
    (source / "small.toml").write_text((mine / "small.toml").read_text().replace("Mine.", "Mine, better."))
    git(source, "commit", "-qam", "second")

    assert packages.update(home) == ["team"]

    now = packages.installed(home)["team"]
    assert now.commit == git(source, "rev-parse", "HEAD") and now.path != first.path and Path(first.path).is_dir()
    started = project(Conductor(home).store.events(run_id=run)).started
    assert started is not None and started["ref"] == str(Path(first.path) / "small.toml")  # the run keeps its version
    assert [r["about"] for r in listed(capsys, "roles", "--home", str(home)) if r["package"] == "team"] == [
        "Mine, better."]
    with pytest.raises(packages.PackageError, match="written for the role API 2"):
        (source / packages.MANIFEST).write_text(MANIFEST.replace("api = 1", "api = 2"))
        packages.manifest(source)


def test_a_run_started_by_the_persons_role_name_records_where_it_lives(
        tmp_path: Path, mine: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    packages.add(home, str(mine))
    monkeypatch.chdir(tmp_path)  # another folder: the run finds the role by its path, not by where it started
    cli.main(["run", "--home", str(home), "--role", "small-change", "--task", "note it", *RUNTIME])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    started = project(Conductor(home).store.events(run_id=out["run"])).started
    assert started is not None and started["ref"] == str(mine / "small.toml")
    with pytest.raises(SystemExit, match="no role 'nope' in the packages this project uses: use one of small-change, "
                                         "direction"):
        cli.main(["run", "--home", str(home), "--role", "nope", "--task", "t"])


def test_a_role_that_plans_starts_its_plan_once_its_run_delivered(
        tmp_path: Path, repo: Path, mine: Path, pending: list[list[str]],  # noqa: F811
        capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    packages.add(home, str(mine), repo)

    cli.main(["run", "--home", str(home), "--role", "direction", "--task", REQUEST, "--project", str(repo),
              *RUNTIME])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    c = Conductor(home)
    st = project(c.store.events(run_id=out["run"]))
    planned = st.step("plan").checked
    assert planned is not None and planned["attempt"] == 2  # the first plan named a role nobody has
    said = json.loads((c.home / "objects" / f"{planned['output_hash']}.json").read_text())
    assert said["why"].startswith("roles shown: small-change, look, screen, build-feature")  # never itself
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
    packages.add(home, str(mine), repo)
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
    packages.add(home, str(mine), repo)
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
    packages.add(home, str(mine), repo)
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


def test_a_run_started_when_roles_came_inside_jaynes_finds_its_role_in_the_package_now() -> None:
    from jaynes import catalog
    from jaynes.role import Role
    for old, name in [("jaynes.roles.build_feature:ROLE", "build-feature"),
                      ("jaynes.roles.screen_design:ROLE", "screen-design")]:
        found = catalog.load(old)
        assert isinstance(found, Role) and found.name == name


def test_the_new_run_page_offers_each_role_once_by_its_package_version_now(tmp_path: Path) -> None:
    from jaynes import catalog
    home = tmp_path / "home"
    c = Conductor(home)
    c.store.append("RunStarted", {"role": "build-feature", "task": "t", "fingerprint": "x", "runtimes": {},
                                  "ref": "jaynes.roles.build_feature:ROLE"}, run_id="r1")  # from before packages
    base, server = serving(home)
    try:
        page = get(f"{base}/new")
    finally:
        server.shutdown()
    now = {e.name: e.ref for e in catalog.entries(home)}
    assert "jaynes.roles.build_feature" not in page and page.count("build-feature (") == 1
    assert f'<option value="{now["build-feature"]}" selected>build-feature (software)</option>' in page


def installed_part(page: str) -> str:
    """The packages part of /roles: from its heading to the roles that ran."""
    return page[page.index("<h1>Packages</h1>"):page.index("Roles that ran")]


def test_the_person_adds_a_package_for_one_project_on_the_roles_page_and_removes_it(
        tmp_path: Path, repo: Path, mine: Path) -> None:  # noqa: F811
    home = tmp_path / "home"
    empty = tmp_path / "empty"
    empty.mkdir()
    base, server = serving(home)
    try:
        page = get(f"{base}/roles")
        part = installed_part(page)
        assert "software" in part and "design" in part and "comes with Jaynes" in part
        assert "fix-bug" in part and "screen-design" in part  # each package with its roles
        assert "every project" in part and 'name="source"' in part and 'name="project"' in part
        token = form(page)["token"]

        status, shown = post(f"{base}/packages", {"token": "stale", "action": "add", "source": str(mine)})
        assert status == 403 and packages.uses(home) == ["software", "design"]
        status, shown = post(f"{base}/packages", {"token": token, "action": "add", "source": str(empty),
                                                   "project": str(repo)})
        assert status == 400 and "has no jaynes-package.toml" in shown and str(empty) in shown  # kept to fix
        status, _ = post(f"{base}/packages", {"token": token, "action": "add", "source": str(mine),
                                              "project": str(repo)})
        assert status == 303
        assert packages.uses(home, repo) == ["team", "software", "design"] and packages.uses(home) == [
            "software", "design"]  # only that project
        part = installed_part(get(f"{base}/roles"))
        assert "team" in part and "0.1.0" in part and str(mine) in part and "direction" in part
        assert str(repo) in part

        status, _ = post(f"{base}/packages", {"token": token, "action": "remove", "name": "team",
                                              "project": str(repo)})
        assert status == 303 and packages.uses(home, repo) == ["software", "design"]
    finally:
        server.shutdown()


def test_an_agent_reads_adds_and_removes_packages_through_the_api_and_the_mcp_server(
        tmp_path: Path, repo: Path, mine: Path) -> None:  # noqa: F811
    home = tmp_path / "home"
    base, server = serving(home)
    try:
        status, out = call(f"{base}/api/add-package", {"source": str(mine), "project": str(repo)})
        assert status == 200, out
        assert out == {"name": "team", "version": "0.1.0", "linked": True, "project": str(repo),
                       "uses": ["team", "software", "design"]}
        with urllib.request.urlopen(f"{base}/api/packages", timeout=10) as r:
            shown = json.loads(r.read())
    finally:
        server.shutdown()
    team = next(p for p in shown["packages"] if p["name"] == "team")
    assert team["source"] == str(mine) and team["linked"] and "direction" in team["roles"]
    assert team["about"] == "A team that plans."
    assert shown["default"] == ["software", "design"] and shown["projects"] == {str(repo): ["team", "software",
                                                                                            "design"]}

    replies = mcp(home, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/call",  # its name argument is the package's
                   "params": {"name": "remove_package", "arguments": {"name": "team", "project": str(repo)}}},
                  tool(3, "packages"),
                  tool(4, "add_package", source=str(tmp_path / "missing")))
    tools = {t["name"]: t for t in replies[0]["result"]["tools"]}
    assert {"packages", "add_package", "remove_package"} <= set(tools)
    assert "asks" in tools["add_package"]["description"]  # only when the person asks for it
    assert not replies[1]["result"]["isError"], replies[1]
    after = json.loads(replies[2]["result"]["content"][0]["text"])
    assert after["projects"] == {str(repo): ["software", "design"]}
    assert replies[3]["result"]["isError"] and packages.uses(home) == ["software", "design"]


def test_the_page_never_adds_a_package_for_every_project_unless_the_person_ticks_it(
        tmp_path: Path, repo: Path, mine: Path) -> None:  # noqa: F811
    home = tmp_path / "home"
    base, server = serving(home)
    try:
        page = get(f"{base}/roles")
        field = re.search(r'<input type="text" name="project"[^>]*>', page)
        assert field is not None and 'value=""' in field[0]  # never the folder the page was opened from
        token = form(page)["token"]
        status, shown = post(f"{base}/packages", {"token": token, "action": "add", "source": str(mine),
                                                   "project": ""})
        assert status == 400 and "Name the project" in shown and packages.uses(home) == ["software", "design"]
        status, _ = post(f"{base}/packages", {"token": token, "action": "add", "source": str(mine),
                                              "project": "", "every": "1"})
        assert status == 303 and packages.uses(home) == ["team", "software", "design"]
    finally:
        server.shutdown()


def test_a_linked_role_changed_on_disk_is_read_again_by_a_process_that_loaded_it(tmp_path: Path) -> None:
    import os
    from jaynes import catalog
    folder = tmp_path / "linked"
    folder.mkdir()
    (folder / "_helper.py").write_text("WORD = 'first'\n")
    role_file = folder / "mine.py"
    role_file.write_text("from jaynes.role import role\nfrom . import _helper\nROLE = role('mine', about=_helper.WORD)\n")
    assert catalog.load(f"{role_file}:ROLE").about == "first"  # type: ignore[attr-defined]
    (folder / "_helper.py").write_text("WORD = 'second'\n")
    role_file.write_text("from jaynes.role import role\nfrom . import _helper\nROLE = role('mine', about=_helper.WORD + '!')\n")
    for p in (folder / "_helper.py", role_file):  # a second later, as an edit is
        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 2))
    assert catalog.load(f"{role_file}:ROLE").about == "second!"  # type: ignore[attr-defined]


def test_the_page_shows_a_linked_role_as_it_is_on_disk_and_the_last_that_imported_while_an_edit_breaks_it(
        tmp_path: Path) -> None:
    import os
    from jaynes.review import Review, Shared
    folder = tmp_path / "shown"
    folder.mkdir()
    role_file = folder / "mine.py"

    def write(text: str, later: int) -> None:
        role_file.write_text(text)
        os.utime(role_file, (role_file.stat().st_atime, role_file.stat().st_mtime + later))  # as an edit, later

    write("from jaynes.role import role\nROLE = role('mine', about='first')\n", 0)
    page = Review(tmp_path / "home", Shared("t", "k", 0))
    first = page.import_role(f"{role_file}:ROLE")
    assert first is not None and first.about == "first"
    write("from jaynes.role import role\nROLE = role('mine', about='second')\n", 2)
    second = page.import_role(f"{role_file}:ROLE")
    assert second is not None and second.about == "second"
    write("from jaynes.role import role\nROLE = role('mine', about=\n", 4)  # half written
    kept = page.import_role(f"{role_file}:ROLE")
    assert kept is not None and kept.about == "second"
