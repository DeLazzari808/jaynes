"""The MCP server (SPEC section 7): the local API's reads and the actions an agent may take, for agents, over
stdio. Claude Code starts it with the plugin as ``jaynes mcp``. Every action it takes is an agent's, so what only
the person may do, such as answering a sample or undoing a delivery, stays on the page; the conductor refuses it
too. It needs no page running: it reads and writes the same log the page does."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import IO, Any, cast

from jaynes import api
from jaynes.review import Review, Shared

RUN = {"type": "string", "description": "the run's id, such as r20261109131915e270dd"}
STEP = {"type": "string", "description": "the step's name, as the run lists it"}
NOTE = {"type": "string", "description": "a sentence, in plain words"}


def _tool(name: str, description: str, properties: dict[str, Any] | None = None,
          required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": list(required)}}


TOOLS = [
    _tool("home", "What Jaynes shows the person on its home page: what holds a run until someone answers, the "
                  "samples only the person answers, the latest deliveries with their hidden checks, and what runs "
                  "now. Each item says who may answer it."),
    _tool("stop", "An open stop of a run: its question, the step's checked output it asks about, the answers it "
                  "takes, the earlier steps it may go back to, and who may answer it.",
          {"run": RUN, "step": STEP}, ("run", "step")),
    _tool("run", "A run: its task, where each step is, what waits in it, and what it delivered.", {"run": RUN},
          ("run",)),
    _tool("answer", "Answer a stop that holds its run, as an agent. accept lets the run go on; change gives the "
                    "edited output; return and context send the step, or an earlier one given in to, back with a "
                    "note; choose keeps the ids given in chosen. A sample, a floor or a braked stop only the person "
                    "answers, on the page. Pass the evidence the stop showed, so an answer to a stop that changed "
                    "since is refused.",
          {"run": RUN, "step": STEP, "answer": {"type": "string", "enum": ["accept", "change", "return", "context",
                                                                          "choose"]},
           "note": NOTE, "output": {"type": "object", "description": "with change: the edited output"},
           "chosen": {"type": "array", "items": {"type": "string"},
                      "description": "with choose: the ids kept, in order of importance"},
           "to": {"type": "string", "description": "with return or context: an earlier step to run again"},
           "evidence": {"type": "string", "description": "the evidence hash the stop showed"}},
          ("run", "step", "answer")),
    _tool("send_back", "Send a blocked run back to an earlier step, with a note saying what was wrong, so it tries "
                       "another way. When two agents disagree about what an earlier step made, the blocked item's "
                       "dispute gives the two choices, each a step and a note to send back.",
          {"run": RUN, "to": STEP, "note": NOTE}, ("run", "to", "note")),
    _tool("continue", "Go on with a run that is not running: blocked, left idle, or past its quota pause.",
          {"run": RUN}, ("run",)),
    _tool("start", "Start a run of a role on a task, in the background. The page shows its stops.",
          {"role": {"type": "string", "description": "a built-in role such as build-feature, fix-bug or "
                                                     "small-change, a role file (.toml) or module:attribute"},
           "task": {"type": "string", "description": "what the run is asked to do, in full"},
           "project": {"type": "string", "description": "a folder in the git repository the run works on"},
           "inputs": {"type": "object", "description": "the inputs the role declares, name to value"}},
          ("role", "task")),
    _tool("correct", "Record that a delivery was corrected afterwards: the step whose output was wrong, what kind "
                     "of wrong, and what. error: wrong by what was already known; preference: a new one nobody had "
                     "written; info: the world changed or a new fact appeared. Only an error counts.",
          {"run": RUN, "step": STEP, "kind": {"type": "string", "enum": ["error", "preference", "info"]},
           "note": NOTE}, ("run", "step", "kind", "note")),
    _tool("halt", "Halt every run before its next step, and every merge. Only the person resumes, on the page.",
          {"note": NOTE}),
]
CALLS = {"send_back": "send-back"}  # a tool's name, where the API's differs


def call(home: Path, name: str, args: dict[str, Any]) -> tuple[bool, Any]:
    """A tool call, as an agent: whether it was done, and what it returned or why not."""
    r = Review(home, Shared(token="", key=""))
    try:
        match name:
            case "home":
                return True, api.home(r)
            case "stop":
                return True, api.stop(r, str(args.get("run", "")), str(args.get("step", "")))
            case "run":
                return True, api.run(r, str(args.get("run", "")))
            case "start":
                return True, api.start(r, {**args, "ref": args.get("role", "")}, "agent")
            case _ if name in {t["name"] for t in TOOLS}:
                return True, api.ACTIONS[CALLS.get(name, name)](r, args, "agent")
            case _:
                return False, f"no tool {name!r}"
    except api.Refused as err:
        return False, err.reason


def handle(home: Path, msg: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message: the reply, or nothing for a notification."""
    method, params, rid = msg.get("method"), cast(dict[str, Any], msg.get("params") or {}), msg.get("id")
    if rid is None:  # a notification, such as notifications/initialized
        return None
    match method:
        case "initialize":  # what it offers is the same in every version, so it speaks the client's
            from jaynes import update
            result: dict[str, Any] = {
                "protocolVersion": str(params.get("protocolVersion") or "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "jaynes", "version": update.RUNNING.short if update.RUNNING else "dev"},
                "instructions": "Jaynes runs agents through a method one step at a time and asks the person only "
                                "where it matters. These tools read what waits and answer for an agent what an "
                                "agent may resolve; the person answers the rest on Jaynes's page."}
        case "ping":
            result = {}
        case "tools/list":
            result = {"tools": TOOLS}
        case "tools/call":
            done, out = call(home, str(params.get("name", "")), cast(dict[str, Any], params.get("arguments") or {}))
            text = json.dumps(out, ensure_ascii=False, indent=1) if done else str(out)
            result = {"content": [{"type": "text", "text": text}], "isError": not done}
        case _:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"no method {method!r}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def serve(home: Path, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
    """Answer messages, one JSON object a line, until stdin closes."""
    if str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))  # role modules are imported from the project, as the page does
    src, out = stdin or sys.stdin, stdout or sys.stdout
    for line in src:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError as err:
            reply: dict[str, Any] | None = {"jsonrpc": "2.0", "id": None,
                                            "error": {"code": -32700, "message": f"not JSON: {err}"}}
        else:
            reply = handle(home, cast(dict[str, Any], msg)) if isinstance(msg, dict) else {
                "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "a message is a JSON object"}}
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False) + "\n")
            out.flush()
