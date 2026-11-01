"""A run's inputs, resolved once when it starts, so editing a file or folder later changes nothing in the run.

A role declares its inputs (``jaynes.role.Input``) and the person gives them as ``jaynes run --input name=value``.
Each is recorded in ``RunStarted`` with a digest, which the steps that list it add to their stop fingerprints:
a new method text, or a measuring tool with looser limits, opens a new window of labels.

* ``text``: kept as given.
* ``file``: the text of the file, read now.
* ``command``: the command line; its digest covers the files it names, such as a script and its limits file.
* ``folder``: copied now into the run's folder and never read live again, as ``source`` or
  ``source:where-in-the-project``. On macOS the copy is a clone, instant and taking no space until it changes.
  Whoever runs the project, such as a preview, gets its own clone of the frozen copy, so a write reaches only it.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jaynes.role import Role

TEXT_BYTES = 1_000_000


class InputError(Exception):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


def missing(role: Role, given: Mapping[str, str]) -> list[str]:
    """Inputs given that the role does not take, and required ones not given, without reading any."""
    reasons = [f"{role.name} takes no input {n!r}; it takes {', '.join(role.inputs) or 'none'}"
               for n in given if n not in role.inputs]
    return reasons + [f"{role.name} needs --input {n}=...: {s.help}" for n, s in role.inputs.items()
                      if s.required and not str(given.get(n, "") or s.default).strip()]


def resolve(role: Role, given: Mapping[str, str], folder: Path) -> dict[str, dict[str, Any]]:
    """Every input of ``role`` as recorded at the start; ``folder`` is where frozen folders go."""
    reasons = [f"{role.name} takes no input {n!r}; it takes {', '.join(role.inputs) or 'none'}"
               for n in given if n not in role.inputs]
    out: dict[str, dict[str, Any]] = {}
    for name, spec in role.inputs.items():
        value = str(given.get(name, "") or spec.default).strip()
        if not value:
            if spec.required:
                reasons.append(f"{role.name} needs --input {name}=...: {spec.help}")
            continue
        try:
            out[name] = _resolve(name, spec.kind, value, folder)
        except (OSError, ValueError) as err:
            reasons.append(f"--input {name}: {err}")
    if reasons:
        raise InputError(reasons)
    return out


def _resolve(name: str, kind: str, value: str, folder: Path) -> dict[str, Any]:
    if kind == "file":
        path = Path(value).expanduser().resolve()
        data = path.read_bytes()
        if len(data) > TEXT_BYTES:
            raise ValueError(f"{path} is larger than {TEXT_BYTES} bytes")
        text = data.decode(errors="replace")
        return {"kind": kind, "value": str(path), "text": text, "digest": _sha(data)}
    if kind == "command":
        named = [p for p in _paths(value) if p.is_file()]
        parts = [value.encode()] + [str(p).encode() + b"\0" + p.read_bytes() for p in named]
        return {"kind": kind, "value": value, "files": [str(p) for p in named], "cwd": str(Path.cwd()),
                "digest": _sha(b"\0".join(parts))}
    if kind == "folder":
        source_text, _, at = value.partition(":")
        source = Path(source_text).expanduser().resolve()
        if not source.is_dir():
            raise ValueError(f"{source} is not a folder")
        at = at.strip().strip("/") or source.name
        if Path(at).is_absolute() or ".." in Path(at).parts:
            raise ValueError(f"{at} must be a path inside the project")
        frozen = folder / name
        clone(source, frozen)
        return {"kind": kind, "value": str(source), "at": at, "frozen": str(frozen), "digest": digest(frozen)}
    return {"kind": kind, "value": value, "digest": _sha(value.encode())}


def _paths(command: str) -> list[Path]:
    """Existing paths a command line names, as words or as the value of --flag=path."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    out: list[Path] = []
    for w in words:
        for part in (w, w.partition("=")[2]):
            if part and ("/" in part or "." in part):
                p = Path(part).expanduser()
                if p.exists():
                    out.append(p.resolve())
    return list(dict.fromkeys(out))


def clone(source: Path, dest: Path) -> None:
    """Copy a folder, as a clone where the disk can: instant, and no space taken until either side changes."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        done = subprocess.run(["cp", "-c", "-R", str(source), str(dest)], capture_output=True)
        if done.returncode == 0:
            return
        if dest.exists():
            shutil.rmtree(dest)
    shutil.copytree(source, dest, symlinks=True)


def digest(folder: Path) -> str:
    """The content of a folder: every path with its bytes, or a link with where it points."""
    h = hashlib.sha256()
    for d, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            p = Path(d) / name
            h.update(p.relative_to(folder).as_posix().encode() + b"\0")
            if p.is_symlink():
                h.update(b"link:" + os.readlink(p).encode())
            else:
                with p.open("rb") as fh:
                    for block in iter(lambda: fh.read(1 << 20), b""):
                        h.update(block)
            h.update(b"\0")
    return h.hexdigest()


def given(started: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return dict(started.get("inputs") or {})


def value(started: Mapping[str, Any], name: str) -> str:
    """An input as the person gave it, or empty."""
    return str(given(started).get(name, {}).get("value", ""))


def folder(started: Mapping[str, Any], name: str) -> str:
    """Where a command input was given: its relative paths are read from there."""
    return str(given(started).get(name, {}).get("cwd", "")) or str(Path.cwd())


def text(started: Mapping[str, Any], name: str) -> str:
    """A file input's text, read when the run started."""
    return str(given(started).get(name, {}).get("text", ""))


def place(started: Mapping[str, Any], project: Path) -> list[str]:
    """Put a fresh clone of every frozen folder where it goes in ``project``; returns where each went."""
    placed: list[str] = []
    for spec in given(started).values():
        if spec.get("kind") == "folder":
            dest = project / str(spec["at"])
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            clone(Path(str(spec["frozen"])), dest)
            placed.append(str(spec["at"]))
    return placed


def fingerprint(started: Mapping[str, Any], names: tuple[str, ...]) -> str:
    """What the inputs a step lists hash to in this run; empty when it lists none."""
    if not names:
        return ""
    found = given(started)
    return _sha("\0".join(f"{n}:{found.get(n, {}).get('digest', '')}" for n in names).encode())[:16]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
