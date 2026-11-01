"""Role definitions: ordered steps with schemas, checks, executors and stops.

A role is plain Python. Each step declares the type of its output (a Pydantic
model), the earlier steps it needs, the checks its output must pass, who
produces it, and whether a person reviews it. Steps can only need steps defined
before them, so the definition order is a topological order of the graph.

Fingerprints hash everything that shapes a step's behaviour (method text,
instructions, schema, check code, stop parameters), so a change opens a new
window of labels.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar, cast, overload

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

OUTPUT = "output.json"


class Pass:
    def __repr__(self) -> str:
        return "Pass()"


class Fail:
    def __init__(self, *reasons: str) -> None:
        if not reasons:
            raise ValueError("Fail needs at least one reason")
        self.reasons = tuple(reasons)

    def __repr__(self) -> str:
        return f"Fail{self.reasons!r}"


@dataclass(frozen=True)
class Checked(Generic[T]):
    """A step output that passed its checks. Only the conductor creates these."""

    step: str
    value: T
    output_hash: str
    check_hash: str


class Blocked(Exception):
    """Raised by a check or a trusted executor when it cannot judge at all, such as a measuring tool that failed:
    the run blocks with the reason at once, and no attempt of the agent is spent, since the fault is not its."""


class Witnesses:
    """The checked outputs a step needs, read by step, and the step's copy of the project if it has one.

    ``source`` is the person's repository, where installed dependencies are linked from when a check runs a
    project command (``jaynes.suite``); ``hidden`` is what such a command may not read, Jaynes's home.
    ``started`` is what the run started with, its inputs among it; ``files`` the files each needed step left,
    by step and path, private ones included.
    """

    def __init__(self, by_name: Mapping[str, Checked[Any]], project: Path | None = None,
                 source: Path | None = None, hidden: tuple[Path, ...] = (),
                 started: Mapping[str, Any] | None = None,
                 files: Mapping[str, Mapping[str, Path]] | None = None) -> None:
        self._by_name = dict(by_name)
        self.project = project
        self.source = source
        self.hidden = hidden
        self.started = dict(started or {})
        self.files = {k: dict(v) for k, v in (files or {}).items()}

    @overload
    def __getitem__(self, step: Step[T]) -> Checked[T]: ...
    @overload
    def __getitem__(self, step: str) -> Checked[Any]: ...  # by name, as a role file's checks read

    def __getitem__(self, step: Step[Any] | str) -> Checked[Any]:
        return self._by_name[step if isinstance(step, str) else step.name]

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def all(self) -> list[Checked[Any]]:
        return [self._by_name[n] for n in self.names()]


Check = Callable[[Any, Witnesses], Pass | Fail]


@dataclass(frozen=True)
class Note:
    """Something said about a step in this run: a person's answer at its stop, or Jaynes sending it back."""

    step: str  # the step whose output it is about
    answer: str  # return, change, context or accept; back when Jaynes sent it back
    note: str
    by: Literal["person", "jaynes"]
    round: int
    reasons: tuple[str, ...] = ()  # what Jaynes found, one per line, when it sent the step back


@dataclass(frozen=True)
class Cost:
    """What a step's agent turns cost in this run, from the runtime's meter. New input excludes the cache read."""

    step: str
    turns: int
    tokens_new: int  # input outside the cache, cache writes and output
    tokens_cached: int  # input read back from the cache
    seconds: float


@dataclass(frozen=True)
class History:
    notes: tuple[Note, ...] = ()
    cost: tuple[Cost, ...] = ()


@dataclass(frozen=True)
class StepContext:
    """What a trusted executor receives. ``idempotency_key`` is set for an irreversible step: pass it to the service
    that acts, so a second call with the same payload in the same run does not act twice."""

    run_id: str
    task: str
    needs: Witnesses
    workspace: Path
    idempotency_key: str = ""
    started: Mapping[str, Any] = field(default_factory=dict[str, Any])  # what the run started with, such as suite
    history: History = field(default_factory=History)  # what was said and spent in the run so far


# What a trusted executor does to the world. Only ``irreversible`` changes how it runs: after a floor stop over the
# payload, once, with a key, never resumed after a crash. The others say what it touches: nothing, what it reads,
# copies it writes, or the web.
Effect = Literal["pure", "read", "write-copy", "web", "irreversible"]
EFFECTS: tuple[Effect, ...] = ("pure", "read", "write-copy", "web", "irreversible")


@dataclass(frozen=True)
class Stop:
    budget: float
    sampled: bool = True
    audit_rate: float = 0.1
    delta: float = 0.05


def stop(budget: float, sampled: bool = True, audit_rate: float = 0.1, delta: float = 0.05) -> Stop:
    if not 0 < budget < 1 or not 0 < delta < 1 or not 0 <= audit_rate <= 1:
        raise ValueError("budget and delta must be in (0, 1), audit_rate in [0, 1]")
    return Stop(budget, sampled, audit_rate, delta)


@dataclass(frozen=True)
class Input:
    """Something a run of the role starts with, given as ``jaynes run --input name=value``.

    ``text`` is kept as given; ``file`` is a text file read once at the start, whose text the steps that list it
    are shown; ``command`` is a command line, fingerprinted with the files it names; ``folder`` is copied once at
    the start, frozen, as ``source`` or ``source:where-in-the-project``.
    """

    help: str
    kind: Literal["text", "file", "command", "folder"] = "text"
    required: bool = True
    default: str = ""


@dataclass(frozen=True)
class Choice:
    """A stop where the person picks among the output's items, in their order of importance.

    ``items`` lists the output's items as (id, label); ``keep`` gives the output with only the chosen, in order.
    """

    items: Callable[[Any], list[tuple[str, str]]]
    keep: Callable[[Any, list[str]], Any]


@dataclass(frozen=True)
class Back:
    """Jaynes sends an earlier step back, with what it found, when a checked output says so.

    ``when(output, needs)`` returns what was found, one reason each; empty means go on. ``to`` and every step after
    it run again, with the reasons in ``to``'s prompt, at most ``rounds`` times in a run; after that the run goes on
    and the later steps, and the person, see what remains.
    """

    to: Step[Any]
    when: Callable[[Any, Witnesses], list[str]]
    rounds: int = 2


@dataclass(eq=False)
class Step(Generic[T]):
    role: Role
    name: str
    output: type[T] | None
    needs: tuple[Step[Any], ...]
    checks: tuple[Check, ...]
    stop: Stop | None
    run_by: Callable[[StepContext], T] | None  # a trusted executor; None means an agent
    agent: str
    instructions: str
    max_turns: int
    deadline: float  # seconds per turn
    question: Callable[[Any, Witnesses], str] | None = None  # what the person is asked at the stop
    project: Literal["read", "write"] | None = None  # a copy of the run's project, to read or to change
    may_change: tuple[str, ...] = ()  # globs of project paths a changing step may touch, never the floor alone
    web: bool = False  # the agent may read the web during this step
    effect: Effect = "pure"  # what a trusted executor does to the world
    ends: Callable[[Any], bool] | None = None  # an output it holds true for ends the run once a person accepts it
    max_messages: int | None = None  # messages per agent turn, where the runtime counts them; None: its default
    asks: Callable[[Any, str], bool] | None = None  # of the output and the step's diff: whether the stop asks at all
    inputs: tuple[str, ...] = ()  # run inputs this step is shown and fingerprinted with
    choose: Choice | None = None  # the stop lets the person pick among the output's items
    back: Back | None = None  # when Jaynes sends an earlier step back from this one
    max_files: int = 20  # files a trusted executor may leave

    @property
    def executor(self) -> str:
        if self.output is None:
            return "none"
        return "jaynes" if self.run_by is not None else self.agent

    def schema(self) -> dict[str, Any]:
        return {} if self.output is None else self.output.model_json_schema()

    def check_hash(self) -> str:
        return _sha("\0".join(code_hash(c) for c in self.checks))

    def prompt_hash(self) -> str:
        """Everything the agent is shown that belongs to the role rather than the task."""
        parts = [self.role.method_text(), self.instructions, json.dumps(self.schema(), sort_keys=True)]
        if self.run_by is not None:
            parts.append(code_hash(self.run_by))
        if self.project:  # appended only when set, so older roles keep their fingerprints
            parts.append(f"project:{self.project}:{','.join(self.may_change)}")
        if self.web:
            parts.append("web")
        if self.effect != "pure":
            parts.append(f"effect:{self.effect}")
        if self.inputs:
            parts.append(f"inputs:{','.join(self.inputs)}")
        return _sha("\0".join(parts))

    def stop_fingerprint(self, model: str, inputs: str = "") -> str:
        """``inputs`` is what the run's inputs this step lists hash to: a new method text opens a new window."""
        assert self.stop is not None
        s = self.stop
        question = [code_hash(self.question)] if self.question else []  # absent, so older windows are kept
        question += [f"ends:{code_hash(self.ends)}"] if self.ends else []
        question += [f"asks:{code_hash(self.asks)}"] if self.asks else []
        question += [f"choose:{code_hash(self.choose.items)}:{code_hash(self.choose.keep)}"] if self.choose else []
        question += [f"inputs:{inputs}"] if inputs else []
        return _sha("\0".join([self.role.name, self.name, self.prompt_hash(), self.check_hash(), *question,
                               repr(s.budget), repr(s.delta), repr(s.sampled), model]))[:16]


@dataclass(eq=False)
class Role:
    name: str
    agent: str = "claude"
    method: str | Path = ""
    steps: list[Step[Any]] = field(default_factory=list[Step[Any]])
    inputs: dict[str, Input] = field(default_factory=dict[str, Input])  # what a run starts with, by name

    def method_text(self) -> str:
        return self.method.read_text() if isinstance(self.method, Path) else self.method

    def step(self, name: str, output: type[T] | None = None, *, needs: Sequence[Step[Any]] = (),
             check: Check | Sequence[Check] = (), stop: Stop | None = None,
             run_by: Callable[[StepContext], T] | None = None, agent: str | None = None,
             instructions: str = "", max_turns: int = 3, deadline: float = 900.0,
             question: Callable[[T, Witnesses], str] | None = None,
             project: Literal["read", "write"] | None = None, may_change: Sequence[str] = (),
             web: bool = False, effect: Effect | None = None, ends: Callable[[T], bool] | None = None,
             max_messages: int | None = None, asks: Callable[[T, str], bool] | None = None,
             inputs: Sequence[str] = (), choose: Choice | None = None, back: Back | None = None,
             max_files: int = 20) -> Step[T]:
        if any(s.name == name for s in self.steps):
            raise ValueError(f"step {name!r} defined twice")
        for n in needs:
            if n.role is not self or n not in self.steps:
                raise ValueError(f"step {name!r} needs {n.name!r}, which is not an earlier step of {self.name!r}")
        if output is None and run_by is not None:
            raise ValueError(f"step {name!r} has an executor but no output type")
        if output is None and stop is None:
            raise ValueError(f"step {name!r} has no output and no stop; it would do nothing")
        if ends is not None and output is None:
            raise ValueError(f"step {name!r} ends the run by its output, but has no output")
        if ends is not None and stop is None:
            raise ValueError(f"step {name!r} ends the run only at a stop, where a person accepts the ending; "
                             "give it a stop")
        if asks is not None and stop is None:
            raise ValueError(f"step {name!r} says when its stop asks, but has no stop")
        if question is not None and stop is None:
            raise ValueError(f"step {name!r} has a question but no stop to ask it at")
        if unknown := [i for i in inputs if i not in self.inputs]:
            raise ValueError(f"step {name!r} lists inputs {', '.join(unknown)}, which {self.name!r} does not declare")
        if choose is not None and (stop is None or output is None):
            raise ValueError(f"step {name!r} lets the person choose, so it needs an output and a stop")
        if back is not None:
            if output is None:
                raise ValueError(f"step {name!r} sends a step back by its output, but has no output")
            if back.to not in self.steps or back.to.output is None:
                raise ValueError(f"step {name!r} sends back {back.to.name!r}, which is not an earlier step of "
                                 f"{self.name!r} with an output")
            if back.rounds < 1:
                raise ValueError(f"step {name!r} sends a step back in at least one round")
        if may_change and project != "write":
            raise ValueError(f"step {name!r} lists may_change but does not change the project")
        if project == "write" and not may_change:
            raise ValueError(f"step {name!r} changes the project but lists no may_change; name the paths it may "
                             "change, or ['**'] for any")
        if project is not None and output is None:
            raise ValueError(f"step {name!r} works on the project but has no output")
        if web and (output is None or run_by is not None):
            raise ValueError(f"step {name!r} reads the web but no agent runs it")
        if effect is not None and effect not in EFFECTS:
            raise ValueError(f"step {name!r} declares effect {effect!r}; it must be one of {', '.join(EFFECTS)}")
        if effect is not None and run_by is None:
            raise ValueError(f"step {name!r} declares an effect but no trusted executor runs it")
        if effect == "irreversible":
            if not needs or not all(n.stop is not None and not n.stop.sampled for n in needs):
                raise ValueError(f"step {name!r} is irreversible; every step it needs must have a floor stop "
                                 "(sampled=False), so a person approves the whole payload")
            if stop is not None and stop.sampled:
                raise ValueError(f"step {name!r} is irreversible, so its own stop must be a floor, never sampled")
            max_turns = 1  # a failed attempt may have acted; a person decides what happens next
        checks = tuple(cast(Sequence[Check], check)) if isinstance(check, Sequence) else (check,)
        s: Step[T] = Step(self, name, output, tuple(needs), checks, stop, run_by, agent or self.agent,
                          instructions, max_turns, deadline, cast(Callable[[Any, Witnesses], str] | None, question),
                          project, tuple(may_change), web, effect or "pure", cast(Callable[[Any], bool] | None, ends),
                          max_messages, cast(Callable[[Any, str], bool] | None, asks), tuple(inputs), choose,
                          back, max_files)
        self.steps.append(s)
        return s

    def fingerprint(self) -> str:
        parts = [self.name] + [
            f"{s.name}:{s.executor}:{s.prompt_hash()}:{s.check_hash()}:{s.stop}:{[n.name for n in s.needs]}"
            + (f":asks:{code_hash(s.asks)}" if s.asks else "")  # appended only when set, so older roles keep theirs
            + (f":choose:{code_hash(s.choose.items)}:{code_hash(s.choose.keep)}" if s.choose else "")
            + (f":back:{s.back.to.name}:{s.back.rounds}:{code_hash(s.back.when)}" if s.back else "")
            for s in self.steps
        ] + [f"input:{n}:{i.kind}:{i.required}:{i.default}" for n, i in self.inputs.items()]
        return _sha("\0".join(parts))[:16]


def role(name: str, agent: str = "claude", method: str | Path = "",
         inputs: Mapping[str, Input] | None = None) -> Role:
    """``method`` is the method text, or a ``Path`` to it, relative paths resolved next to the calling module."""
    if isinstance(method, Path) and not method.is_absolute():
        method = Path(inspect.stack()[1].filename).resolve().parent / method
    return Role(name, agent, method, inputs=dict(inputs or {}))


def code_hash(fn: Callable[..., Any]) -> str:
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        src = getattr(fn, "__qualname__", repr(fn))
    return _sha(src)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
