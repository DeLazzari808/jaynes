"""Packages of roles. The engine brings no role of its own: every set of roles is a package, installed into the home
at a fixed version and used by the projects that list it, as npm installs libraries for a project.

A package is a folder with ``jaynes-package.toml``: its name, version, what it is for, and the role API it is written
against. Every role file and every Python file with a role in that folder is one of its roles; a Python file imports
the files next to it relatively, ``from . import other``, so two versions of a package never mix.

A package comes from one of three places: the ones that come with Jaynes, by name (``software``, ``design``), which
follow Jaynes's own version; a git repository, at its newest commit or a given one, updated by ``jaynes update``; or a
folder of the person's, linked, so it is used where it is and an edit applies at once, as ``npm link`` does.

Every installed version keeps its own folder, so a run goes on with the version it started with. Each project lists
the packages it uses, in the home, never in the project; a project with no list of its own uses the default one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

MANIFEST = "jaynes-package.toml"
API = 1  # the role API: what a role may import from Jaynes, as SPEC.md lists it; a change that breaks a role bumps it
FILE = "packages.json"  # in the home
DEFAULT = ("software", "design")  # what a new home uses everywhere
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
SKIPPED = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".DS_Store")


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    about: str
    api: int


def manifest(folder: Path) -> Manifest:
    path = folder / MANIFEST
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise PackageError(f"{folder} is not a package: it has no {MANIFEST}") from None
    except tomllib.TOMLDecodeError as err:
        raise PackageError(f"{path}: {err}") from None
    name, version = str(data.get("name", "")), str(data.get("version", ""))
    if not NAME.fullmatch(name):
        raise PackageError(f"{path}: name must be lowercase letters, digits and dashes, such as my-team")
    if not version:
        raise PackageError(f"{path}: it needs a version, such as 1.0.0")
    api = data.get("api")
    if api != API:
        raise PackageError(f"{path}: written for the role API {api!r}, and this Jaynes has {API}; update the "
                           "package or Jaynes")
    return Manifest(name, version, str(data.get("about", "")), API)


def bundled_root() -> Path:
    """Where the packages that come with Jaynes are: inside the installed Jaynes, or next to its source."""
    inside = Path(__file__).resolve().parent / "_packages"
    return inside if inside.is_dir() else Path(__file__).resolve().parents[2] / "packages"


def bundled(name: str) -> Path | None:
    if not NAME.fullmatch(name):
        return None
    folder = bundled_root() / name
    return folder if (folder / MANIFEST).is_file() else None


@dataclass(frozen=True)
class Installed:
    name: str
    version: str
    path: str  # the folder its roles are read from
    source: str  # "jaynes" for one that comes with Jaynes, a git address, or the linked folder
    linked: bool = False
    commit: str = ""  # for one from git
    ref: str = ""  # the branch, tag or commit asked for, if any
    folder: str = ""  # the folder in the repository that holds it, if not its top


def _content(folder: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                       and p.suffix != ".pyc" and ".git" not in p.parts):
        h.update(str(path.relative_to(folder)).encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()[:10]


def _state(home: Path) -> dict[str, Any]:
    path = home / FILE
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return cast(dict[str, Any], data)
    except FileNotFoundError:
        pass
    except ValueError as err:
        raise PackageError(f"{path} is not JSON: {err}") from None
    state: dict[str, Any] = {"installed": {}, "default": [], "projects": {}}  # a new home: what comes with Jaynes
    for name in DEFAULT:
        if (folder := bundled(name)) is not None:
            state["installed"][name] = asdict(_copy(home, folder, "jaynes"))
            state["default"].append(name)
    _save(home, state)
    return state


def _save(home: Path, state: dict[str, Any]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    tmp = home / f"{FILE}.{os.getpid()}"
    tmp.write_text(json.dumps(state, indent=1) + "\n")
    tmp.replace(home / FILE)


def _copy(home: Path, folder: Path, source: str, commit: str = "", ref: str = "", sub: str = "") -> Installed:
    """Install the package in ``folder`` as a version of its own, kept while any run may use it."""
    m = manifest(folder)
    version = f"{m.version}-{commit[:10] if commit else _content(folder)}"
    target = home / "packages" / m.name / version
    if not target.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".installing-", dir=target.parent))
        shutil.copytree(folder, tmp / "p", ignore=SKIPPED)
        try:
            (tmp / "p").rename(target)
        except OSError:  # another process installed it first
            if not target.is_dir():
                raise
        shutil.rmtree(tmp, ignore_errors=True)
    return Installed(m.name, m.version, str(target), source, commit=commit, ref=ref, folder=sub)


def _git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise PackageError(f"git {' '.join(args[:2])} failed: {(done.stderr or done.stdout).strip()[:500]}")
    return done.stdout.strip()


def _from_git(home: Path, url: str, ref: str = "", sub: str = "") -> Installed:
    work = Path(tempfile.mkdtemp(prefix="jaynes-package-"))
    try:
        _git("clone", "--quiet", url, str(work / "repo"))
        if ref:
            _git("checkout", "--quiet", ref, cwd=work / "repo")
        commit = _git("rev-parse", "HEAD", cwd=work / "repo")
        return _copy(home, work / "repo" / sub if sub else work / "repo", url, commit, ref, sub)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def installed(home: Path) -> dict[str, Installed]:
    """Every installed package by name; one that comes with Jaynes follows the Jaynes installed now."""
    state = _state(home)
    found: dict[str, Installed] = {}
    changed = False
    for name, raw in cast(dict[str, dict[str, Any]], state.get("installed") or {}).items():
        p = Installed(**raw)
        if p.source == "jaynes" and (folder := bundled(name)) is not None:
            try:
                now = _copy(home, folder, "jaynes")
            except PackageError:
                now = p
            if now.path != p.path:
                p, changed = now, True
                state["installed"][name] = asdict(p)
        found[name] = p
    if changed:
        _save(home, state)
    return found


def root(project: Path) -> str:
    """The project as packages are listed for it: its git repository's top folder, or the folder itself."""
    from jaynes.project import ProjectError, resolve
    try:
        return str(resolve(project)[0])
    except ProjectError:
        return str(project.expanduser().resolve())


def uses(home: Path, project: Path | None = None) -> list[str]:
    """The packages a project uses, in order: its own list, or the default one."""
    state = _state(home)
    lists = cast(dict[str, list[str]], state.get("projects") or {})
    if project is not None and (own := lists.get(root(project))) is not None:
        return list(own)
    return list(cast(list[str], state.get("default") or []))


def add(home: Path, source: str, project: Path | None = None, ref: str = "", folder: str = "") -> Installed:
    """Install a package, or link a folder, and put it first in the project's list, or in the default one. A
    project with no list of its own starts from the default one."""
    state = _state(home)
    if bundled(source) is not None and not ref and not folder:
        p = _copy(home, cast(Path, bundled(source)), "jaynes")
    elif Path(source).expanduser().is_dir() and not ref:
        where = Path(source).expanduser().resolve() / folder if folder else Path(source).expanduser().resolve()
        m = manifest(where)
        p = Installed(m.name, m.version, str(where), str(where), linked=True)
    else:
        p = _from_git(home, source, ref, folder)
    known = cast(dict[str, Any], state.setdefault("installed", {}))
    if p.name in known and Installed(**known[p.name]).source != p.source:
        raise PackageError(f"a package named {p.name} is already installed from {known[p.name]['source']}; remove "
                           "it first")
    known[p.name] = asdict(p)
    if project is None:
        state["default"] = [p.name, *[n for n in cast(list[str], state.get("default") or []) if n != p.name]]
    else:
        lists = cast(dict[str, list[str]], state.setdefault("projects", {}))
        mine = lists.get(root(project), list(state.get("default") or []))
        lists[root(project)] = [p.name, *[n for n in mine if n != p.name]]
    _save(home, state)
    return p


def remove(home: Path, name: str, project: Path | None = None) -> None:
    """Take the package out of the project's list, or out of the default one. Its installed versions stay for the
    runs that use them."""
    state = _state(home)
    if project is None:
        if name not in (state.get("default") or []):
            raise PackageError(f"{name} is not in the default list")
        state["default"] = [n for n in cast(list[str], state["default"]) if n != name]
    else:
        lists = cast(dict[str, list[str]], state.setdefault("projects", {}))
        mine = lists.get(root(project), list(state.get("default") or []))
        if name not in mine:
            raise PackageError(f"{root(project)} does not use {name}")
        lists[root(project)] = [n for n in mine if n != name]
    _save(home, state)


def update(home: Path) -> list[str]:
    """Bring each package from git to the newest commit of what it follows; the names that changed. A pinned commit
    stays; the ones that come with Jaynes follow Jaynes, and a linked folder is always as it is."""
    state = _state(home)
    changed: list[str] = []
    for name, raw in cast(dict[str, dict[str, Any]], state.get("installed") or {}).items():
        p = Installed(**raw)
        if p.linked or p.source == "jaynes" or (p.ref and re.fullmatch(r"[0-9a-f]{7,40}", p.ref)):
            continue
        now = _from_git(home, p.source, p.ref, p.folder)
        if now.path != p.path:
            state["installed"][name] = asdict(now)
            changed.append(name)
    if changed:
        _save(home, state)
    return changed


def projects(home: Path) -> dict[str, list[str]]:
    return dict(cast(dict[str, list[str]], _state(home).get("projects") or {}))
