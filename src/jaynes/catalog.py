"""Where roles come from: the ones that come with Jaynes, and the folders of roles the person added.

A person keeps their own roles, such as a company's private specialists, in a folder outside Jaynes and adds it once:
``jaynes roles add <folder>``. Every role file (``.toml``) and every Python file with a role in it there is a role
started by its name, and one named like a built-in role takes its place. A run records the role's reference whole,
a file's absolute path, so it continues from any folder and in any process.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import cast

from jaynes.role import Role

FILE = "roles.json"  # in the home: {"folders": [absolute paths]}


class Unknown(ValueError):
    pass


def folders(home: Path) -> list[Path]:
    path = home / FILE
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    found: object = cast(dict[str, object], data).get("folders") if isinstance(data, dict) else None
    return [Path(str(f)) for f in cast(list[object], found)] if isinstance(found, list) else []


def _save(home: Path, kept: list[Path]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / FILE).write_text(json.dumps({"folders": [str(f) for f in kept]}, indent=1) + "\n")


def add(home: Path, folder: Path) -> Path:
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise Unknown(f"{folder} is not a folder")
    kept = folders(home)
    if folder not in kept:
        _save(home, [*kept, folder])
    return folder


def remove(home: Path, folder: Path) -> bool:
    folder = folder.expanduser().resolve()
    kept = folders(home)
    if folder not in kept:
        return False
    _save(home, [f for f in kept if f != folder])
    return True


def _module(path: Path) -> ModuleType:
    """A Python file imported by its path. Its folder is on the import path, after everything else, so the file can
    import the files next to it by name; under that same name, so a role imported twice is one role."""
    folder = str(path.parent)
    if folder not in sys.path:
        sys.path.append(folder)
    name = path.stem
    known = sys.modules.get(name)
    if known is not None:
        if Path(str(getattr(known, "__file__", "") or "")).resolve() == path:
            return known
        name = f"jaynes_roles_{abs(hash(str(path)))}_{path.stem}"
        if name in sys.modules:
            return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def load(reference: str) -> object:
    """What a reference names: a role file's path, a Python file's ``path.py:attribute``, or ``module:attribute``,
    imported from the current folder too, as ``jaynes run`` always allowed."""
    if reference.endswith(".toml"):
        from jaynes.role_file import load as load_file
        return load_file(Path(reference))
    where, _, attr = reference.rpartition(":")
    if not where or not attr:
        raise ImportError(f"expected module:attribute, got {reference!r}")
    if where.endswith(".py"):
        return getattr(_module(Path(where).resolve()), attr)
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    return getattr(importlib.import_module(where), attr)


@dataclass(frozen=True)
class Entry:
    """One role a run can start by name."""

    name: str
    ref: str
    about: str
    folder: str  # the person's folder it comes from, or "" for one that comes with Jaynes
    plans: bool  # its delivery is a plan of runs, which Jaynes starts by itself


def plans(r: Role) -> bool:
    from jaynes.plan import Plan
    return any(s.output is not None and issubclass(s.output, Plan) for s in r.steps)


def _found(folder: Path) -> list[tuple[str, Role]]:
    """The roles in one of the person's folders, with their references; a file that does not load is left out."""
    out: list[tuple[str, Role]] = []
    for path in sorted(folder.glob("*.toml")):
        try:
            r = load(str(path))
        except Exception:
            continue
        if isinstance(r, Role):
            out.append((str(path), r))
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module = _module(path.resolve())
        except Exception:
            continue
        for attr, value in vars(module).items():
            if isinstance(value, Role) and not attr.startswith("_"):
                out.append((f"{path.resolve()}:{attr}", value))
    return out


def entries(home: Path) -> list[Entry]:
    """Every role a run can start by name: the person's first, folder by folder and file by file, then the built-in
    ones whose name no role of theirs took. A name is listed once, from the first file that has it."""
    from jaynes.roles import BUILT_IN
    out: dict[str, Entry] = {}
    for folder in folders(home):
        if not folder.is_dir():
            continue
        for reference, r in _found(folder):
            if r.name not in out:
                out[r.name] = Entry(r.name, reference, r.about, str(folder), plans(r))
    for name, reference in BUILT_IN.items():
        if name in out:
            continue
        try:
            found = load(reference)
        except Exception:
            continue
        if isinstance(found, Role):
            out[name] = Entry(name, reference, found.about, "", plans(found))
    return list(out.values())


def ref(name: str, home: Path) -> str:
    """A role's reference from what a person or an agent names it by: one of their roles or a built-in one by its
    name, a role file, a Python file's ``path.py:attribute``, or ``module:attribute``."""
    if name.endswith(".toml"):
        return str(Path(name).expanduser().resolve())
    if ":" in name:
        where, _, attr = name.rpartition(":")
        return f"{Path(where).expanduser().resolve()}:{attr}" if where.endswith(".py") else name
    known = {e.name: e.ref for e in entries(home)}
    if name not in known:
        raise Unknown(f"no role {name!r}: use one of {', '.join(known)}, a role file (.toml), or module:attribute")
    return known[name]
