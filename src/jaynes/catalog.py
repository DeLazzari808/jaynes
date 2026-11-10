"""Where a run's role comes from: the packages the project uses (``jaynes.packages``), by the role's name, or a
reference to a file or a module.

A package's Python files are imported as one Python package per installed version, named after its folder, so a
file imports the files next to it relatively and two versions of a package never mix. A run records its role's file
by its full path, which names the version, so it continues from any folder and in any process with the version it
started with.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from jaynes import packages
from jaynes.role import Role

LEGACY = {"jaynes.roles.build_feature": ("software", "build_feature"), "jaynes.roles.fix_bug": ("software", "fix_bug"),
          "jaynes.roles.small_change": ("software", "small_change"),
          "jaynes.roles.visual_identity": ("design", "visual_identity"), "jaynes.roles.redesign": ("design", "redesign"),
          "jaynes.roles.screen_design": ("design", "screen_design"),
          "jaynes.roles.references": ("design", "references")}  # where runs started before packages find their role


class Unknown(ValueError):
    pass


def _package(folder: Path) -> str:
    """The Python package an installed version's folder is imported as."""
    name = "_jaynes_package_" + hashlib.sha256(str(folder).encode()).hexdigest()[:12]
    if name not in sys.modules:
        spec = importlib.util.spec_from_loader(name, loader=None, is_package=True)
        assert spec is not None
        made = importlib.util.module_from_spec(spec)
        made.__path__ = [str(folder)]
        sys.modules[name] = made
    return name


def module(path: Path) -> ModuleType:
    """A role's Python file, imported as part of its folder's package."""
    path = path.resolve()
    return importlib.import_module(f"{_package(path.parent)}.{path.stem}")


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
        return getattr(module(Path(where)), attr)
    if where in LEGACY:  # a run started when these roles came inside Jaynes: the version that comes with it now
        package, stem = LEGACY[where]
        folder = packages.bundled(package)
        if folder is None:
            raise ImportError(f"{where} moved to the package {package}, which this Jaynes does not have")
        return getattr(module(folder / f"{stem}.py"), attr)
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    return getattr(importlib.import_module(where), attr)


@dataclass(frozen=True)
class Entry:
    """One role a run can start by name."""

    name: str
    ref: str
    about: str
    package: str
    plans: bool  # its delivery is a plan of runs, which Jaynes starts by itself


def plans(r: Role) -> bool:
    from jaynes.plan import Plan
    return any(s.output is not None and issubclass(s.output, Plan) for s in r.steps)


def found(folder: Path) -> list[tuple[str, Role]]:
    """The roles in a package's folder, with their references; a file that does not load is left out."""
    out: list[tuple[str, Role]] = []
    for path in sorted(folder.glob("*.toml")):
        if path.name == packages.MANIFEST:
            continue
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
            m = module(path)
        except Exception:
            continue
        for attr, value in vars(m).items():
            if isinstance(value, Role) and not attr.startswith("_"):
                out.append((f"{path.resolve()}:{attr}", value))
    return out


def entries(home: Path, project: Path | None = None) -> list[Entry]:
    """Every role a run on ``project`` can start by name, package by package in the project's order; a name is
    listed once, from the first package that has it."""
    out: dict[str, Entry] = {}
    have = packages.installed(home)
    for name in packages.uses(home, project):
        p = have.get(name)
        if p is None or not Path(p.path).is_dir():
            continue
        for reference, r in found(Path(p.path)):
            if r.name not in out:
                out[r.name] = Entry(r.name, reference, r.about, name, plans(r))
    return list(out.values())


def ref(name: str, home: Path, project: Path | None = None) -> str:
    """A role's reference from what a person or an agent names it by: a role of the project's packages by its name,
    a role file, a Python file's ``path.py:attribute``, or ``module:attribute``."""
    if name.endswith(".toml"):
        return str(Path(name).expanduser().resolve())
    if ":" in name:
        where, _, attr = name.rpartition(":")
        return f"{Path(where).expanduser().resolve()}:{attr}" if where.endswith(".py") else name
    known = {e.name: e.ref for e in entries(home, project)}
    if name not in known:
        raise Unknown(f"no role {name!r} in the packages this project uses: use one of {', '.join(known) or 'none'}, "
                      "add a package with jaynes add, or give a role file (.toml) or module:attribute")
    return known[name]
