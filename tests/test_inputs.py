"""What a run starts with: inputs a role declares, read and frozen once, shown to the steps that list them."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from jaynes import inputs
from jaynes.conductor import Conductor
from jaynes.inputs import InputError
from jaynes.role import Input, Role, Step, role, stop
from tests.fakes import Scripted


class Note(BaseModel):
    text: str


def make() -> tuple[Role, Step[Note], Step[Note]]:
    r = role("with-inputs", agent="fake", inputs={
        "method": Input("the method text", kind="file"), "theme": Input("the owner's theme", default="dark"),
        "data": Input("the data the product reads", kind="folder", required=False),
        "measure": Input("measures a screen", kind="command", required=False)})
    plain = r.step("plain", output=Note)
    shown = r.step("shown", output=Note, needs=[plain], stop=stop(0.2), inputs=["method", "theme"])
    return r, plain, shown


def test_a_run_needs_every_required_input_and_takes_no_unknown_one(tmp_path: Path) -> None:
    r, _, _ = make()
    c = Conductor(tmp_path, {"fake": lambda: Scripted(default=lambda s: {"text": s})})
    with pytest.raises(InputError, match="needs --input method"):
        c.run(r, "t", started={"inputs": {}})
    with pytest.raises(InputError, match="takes no input 'colour'"):
        c.run(r, "t", started={"inputs": {"method": str(tmp_path), "colour": "red"}})
    assert c.store.runs() == []  # refused before the run exists


def test_a_file_input_is_read_once_and_shown_only_to_the_steps_that_list_it(tmp_path: Path) -> None:
    r, _, _ = make()
    method = tmp_path / "method.md"
    method.write_text("Ask about the moments first.")
    agent = Scripted(default=lambda s: {"text": s})
    c = Conductor(tmp_path / "home", {"fake": lambda: agent})
    out = c.run(r, "t", started={"inputs": {"method": str(method)}})
    assert out.status == "waiting"
    plain, shown = agent.prompts
    assert "Ask about the moments first." not in plain and "theme: dark" not in plain
    assert "Ask about the moments first." in shown and "theme: dark" in shown
    method.write_text("Changed after the start.")
    [started] = [e.payload for e in c.store.events("RunStarted")]
    assert started["inputs"]["method"]["text"] == "Ask about the moments first."


def test_a_new_method_text_opens_a_new_window_at_the_steps_that_list_it(tmp_path: Path) -> None:
    r, _, shown = make()
    one, two = tmp_path / "one.md", tmp_path / "two.md"
    one.write_text("one")
    two.write_text("two")
    fps: list[str] = []
    for i, m in enumerate((one, one, two)):
        c = Conductor(tmp_path / f"home{i}", {"fake": lambda: Scripted(default=lambda s: {"text": s})})
        c.run(r, "t", started={"inputs": {"method": str(m)}})
        [opened] = [e.payload for e in c.store.events("StopOpened")]
        fps.append(opened["fingerprint"])
    assert fps[0] == fps[1] != fps[2]
    assert shown.stop_fingerprint("fake-1") not in fps


def test_a_folder_is_frozen_at_the_start_and_each_project_gets_its_own_clone(tmp_path: Path) -> None:
    r, _, _ = make()
    data = tmp_path / "live"
    (data / "board").mkdir(parents=True)
    (data / "board" / "items.json").write_text('[{"id": 1}]')
    method = tmp_path / "m.md"
    method.write_text("m")
    c = Conductor(tmp_path / "home", {"fake": lambda: Scripted(default=lambda s: {"text": s})})
    c.run(r, "t", started={"inputs": {"method": str(method), "data": f"{data}:data"}})
    [started] = [e.payload for e in c.store.events("RunStarted")]
    spec = started["inputs"]["data"]
    (data / "board" / "items.json").write_text('[{"id": 1}, {"id": 2}]')  # the person keeps working
    assert inputs.digest(Path(spec["frozen"])) == spec["digest"] != inputs.digest(data)
    project = tmp_path / "copy"
    project.mkdir()
    assert inputs.place(started, project) == ["data"]
    (project / "data" / "board" / "items.json").write_text("written by the product")
    assert (Path(spec["frozen"]) / "board" / "items.json").read_text() == '[{"id": 1}]'


def test_a_command_is_fingerprinted_with_the_files_it_names(tmp_path: Path) -> None:
    r, _, _ = make()
    kit, limits = tmp_path / "kit.py", tmp_path / "limits.json"
    kit.write_text("print('{}')")
    limits.write_text('{"sizes": 5}')
    given = {"method": str(kit), "measure": f"python3 {kit} --limits={limits} {{url}}"}
    first = inputs.resolve(r, given, tmp_path / "a")
    assert inputs.resolve(r, given, tmp_path / "b")["measure"]["digest"] == first["measure"]["digest"]
    limits.write_text('{"sizes": 6}')  # a looser limit
    assert inputs.resolve(r, given, tmp_path / "c")["measure"]["digest"] != first["measure"]["digest"]
    assert first["measure"]["files"] == [str(kit.resolve()), str(limits.resolve())]


def test_a_step_can_list_only_inputs_its_role_declares() -> None:
    r = role("x", inputs={"method": Input("m", kind="file")})
    with pytest.raises(ValueError, match="does not declare"):
        r.step("a", output=Note, inputs=["heuristics"])
