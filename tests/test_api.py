"""The local API and the MCP server: every action of the page, as JSON, for the person or an agent. An agent
answers what holds a run; only the person answers a sample, drops a run, undoes, files a correction or resumes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

from jaynes.store import Store
from tests.test_home import held, merged
from tests.test_review import JAR, get, post, serving

ROOT = Path(__file__).resolve().parent.parent


def call(url: str, body: dict[str, Any], jar: CookieJar | None = None, kind: str = "application/json",
         origin: str | None = None) -> tuple[int, Any]:
    """A JSON request, as an agent unless it carries the person's cookie jar."""
    headers = {"Content-Type": kind, **({"Origin": origin} if origin else {})}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    opener = urllib.request.build_opener(*([urllib.request.HTTPCookieProcessor(jar)] if jar else []))
    try:
        with opener.open(req, timeout=10) as r:
            status, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read().decode()
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def answers(home: Path, run: str) -> list[dict[str, Any]]:
    return [e.payload for e in Store(home / "jaynes.sqlite3").events("StopAnswered", run_id=run)]


def test_an_agent_answers_what_holds_a_run_and_only_the_person_answers_a_sample(tmp_path: Path) -> None:
    home = tmp_path / "home"
    waiting = held(home, "write the draft")
    went_on = merged(home, tmp_path)
    base, server = serving(home)
    try:
        status, out = call(base + "/api/answer", {"run": went_on, "step": "accept", "answer": "accept"})
        assert status == 403 and "sample" in out["error"] and answers(home, went_on) == []
        status, out = call(base + "/api/answer", {"run": waiting, "step": "agree", "answer": "accept"})
        assert status == 200 and out == {"run": waiting, "step": "agree", "answer": "accept", "by": "agent",
                                         "continuing": True}
        assert [a["by"] for a in answers(home, waiting)] == ["agent"]
        status, out = call(base + "/api/answer", {"run": went_on, "step": "accept", "answer": "accept"}, jar=JAR)
        assert status == 200 and out["by"] == "person"
        assert [a["by"] for a in answers(home, went_on)] == ["person"]
    finally:
        server.shutdown()


def test_what_only_the_person_may_do_is_refused_to_an_agent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    went_on = merged(home, tmp_path)
    waiting = held(home, "write the draft")
    base, server = serving(home)
    try:
        for action, body in [("undo", {"run": went_on, "note": "wrong"}), ("dismiss", {"run": waiting}),
                             ("correction", {"run": went_on, "id": "x", "kind": "info"}), ("resume", {})]:
            status, out = call(base + f"/api/{action}", body)
            assert status == 403 and out["error"].startswith("only the person"), action
        assert call(base + "/api/dismiss", {"run": waiting}, jar=JAR) == (200, {"run": waiting, "dismissed": True})
        assert call(base + "/api/halt", {"note": "looking"}) == (200, {"halted": True})  # an agent may halt
        assert call(base + "/api/resume", {})[0] == 403
        assert call(base + "/api/resume", {}, jar=JAR) == (200, {"resumed": [went_on]})  # the runs it goes on with
    finally:
        server.shutdown()


def test_the_api_takes_json_from_this_computer_alone(tmp_path: Path) -> None:
    home = tmp_path / "home"
    waiting = held(home, "write the draft")
    base, server = serving(home)
    try:
        status, out = post(base + "/api/answer", {"run": waiting, "step": "agree", "answer": "accept"})
        assert status == 415 and "JSON" in out  # another site's form cannot post here
        status, out = call(base + "/api/answer", {"run": waiting, "step": "agree", "answer": "accept"},
                           origin="https://example.com")
        assert status == 403 and out == "forbidden"
        status, out = call(base + "/api/nothing", {})
        assert status == 404 and "answer" in out["actions"]
        status, out = call(base + "/api/answer", {"run": waiting, "step": "agree", "answer": "maybe"})
        assert status == 400 and out == {"error": "unknown answer 'maybe'"}
        assert answers(home, waiting) == []
    finally:
        server.shutdown()


def test_the_api_reads_what_the_home_page_shows_a_stop_and_a_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    waiting = held(home, "write the draft")
    went_on = merged(home, tmp_path)
    base, server = serving(home)
    try:
        shown = json.loads(get(base + "/api/home"))
        stop = json.loads(get(base + f"/api/stop/{waiting}/agree"))
        run = json.loads(get(base + f"/api/run/{went_on}"))
        status, refused = call(base + "/api/answer", {})  # an empty answer: refused, not a crash
    finally:
        server.shutdown()
    assert shown["halted"] is None and shown["running"] == []
    [w] = shown["waiting"]
    assert (w["run"], w["step"], w["holding"], w["who_answers"]) == (waiting, "agree", True, "anyone")
    assert w["question"] == "Is the draft what you asked?" and w["page"].endswith(f"/stop/{waiting}/agree")
    [s] = shown["samples"]
    assert (s["run"], s["sample"], s["who_answers"]) == (went_on, True, "the person")
    [d] = shown["delivered"]
    assert (d["run"], d["landed"], d["into"], d["hidden_checks"], d["samples"]) == (
        went_on, "merged", "main", {"passed": 1, "of": 1}, 1)
    assert stop["question"] == "Is the draft what you asked?" and stop["earlier"] == ["draft"]
    assert stop["evidence"] and "accept" in stop["answers"]
    assert run["delivered"]["landed"] == "merged" and [x["name"] for x in run["steps"]] == ["cases", "make", "prove", "accept"]
    assert status == 400 and refused == {"error": "unknown answer ''"}


def mcp(home: Path, *messages: dict[str, Any]) -> list[dict[str, Any]]:
    """The replies of ``jaynes mcp`` to ``messages``, as Claude Code sends them, one a line."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT)]))
    done = subprocess.run([sys.executable, "-m", "jaynes.cli", "mcp", "--home", str(home)], cwd=ROOT, env=env,
                          input="".join(json.dumps(m) + "\n" for m in messages), capture_output=True, text=True,
                          timeout=60, check=True)
    return [json.loads(line) for line in done.stdout.splitlines()]


def tool(i: int, name: str, **arguments: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


def test_the_mcp_server_gives_an_agent_the_reads_and_the_answers_it_may_give(tmp_path: Path) -> None:
    home = tmp_path / "home"
    waiting = held(home, "write the draft")
    went_on = merged(home, tmp_path)
    replies = mcp(home,
                  {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "test", "version": "0"}}},
                  {"jsonrpc": "2.0", "method": "notifications/initialized"},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                  tool(3, "home"),
                  tool(4, "answer", run=went_on, step="accept", answer="accept"),
                  tool(5, "answer", run=waiting, step="agree", answer="accept"),
                  tool(6, "undo", run=went_on, note="wrong"))
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6]  # the notification has no reply
    first = replies[0]["result"]
    assert first["protocolVersion"] == "2025-06-18" and first["serverInfo"]["name"] == "jaynes"
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert names == ["home", "stop", "run", "answer", "send_back", "continue", "start", "correct", "halt", "packages",
                     "add_package", "remove_package"]
    shown = json.loads(replies[2]["result"]["content"][0]["text"])
    assert [w["run"] for w in shown["waiting"]] == [waiting] and [s["run"] for s in shown["samples"]] == [went_on]
    sample = replies[3]["result"]
    assert sample["isError"] and "only the person answers it" in sample["content"][0]["text"]
    assert not replies[4]["result"]["isError"]
    assert replies[5]["result"]["isError"] and "no tool 'undo'" in replies[5]["result"]["content"][0]["text"]
    assert [a["by"] for a in answers(home, waiting)] == ["agent"] and answers(home, went_on) == []


def test_the_plugin_starts_the_mcp_server_with_the_installed_jaynes(tmp_path: Path) -> None:
    server = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["mcpServers"]["jaynes"]
    found = tmp_path / ".local" / "bin"
    found.mkdir(parents=True)
    (found / "jaynes").write_text('#!/bin/sh\necho "jaynes $*"\n')
    (found / "jaynes").chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}  # not on the PATH, as when uv's folder is missing there
    done = subprocess.run([server["command"], *server["args"]], env=env, capture_output=True, text=True, timeout=10)
    assert done.stdout == "jaynes mcp\n"
    (found / "jaynes").unlink()
    done = subprocess.run([server["command"], *server["args"]], env=env, capture_output=True, text=True, timeout=10)
    assert done.returncode == 1 and "not installed" in done.stderr


def test_an_agent_starts_a_run_through_the_api(tmp_path: Path) -> None:
    home = tmp_path / "home"
    held(home, "write the draft")  # the role and its runtime are known from it, as the page's New run knows them
    base, server = serving(home)
    try:
        status, out = call(base + "/api/start", {"ref": "tests.held:ROLE", "task": "write a second one"})
    finally:
        server.shutdown()
    assert status == 200 and out["started"]
    deadline = time.time() + 30
    while time.time() < deadline and not list(Store(home / "jaynes.sqlite3").events("StopOpened", run_id=out["run"])):
        time.sleep(0.2)
    [started] = [e.payload for e in Store(home / "jaynes.sqlite3").events("RunStarted", run_id=out["run"])]
    assert started["task"] == "write a second one"
