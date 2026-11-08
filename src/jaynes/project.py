"""A run's project: the git repository a run works on, without ever touching its working tree.

A run records the repository root and its last commit (the base). A step that reads or changes the project
gets a copy of the base with the patches of earlier steps applied. A private git directory beside the
workspace, out of the agent's reach, turns what the step changed into a patch. When the run finishes, the
patches become one commit each on a branch built with a temporary index, so the person's checkout, index
and uncommitted work stay as they were.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Sequence
from fnmatch import fnmatchcase
from pathlib import Path

# Never let a person's git config (hooks, signing, templates) act on Jaynes's own git calls.
SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "init.templateDir=",
        "-c", "user.name=Jaynes", "-c", "user.email=jaynes@localhost", "-c", "core.autocrlf=false",
        "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false"]  # patches always read a/ and b/


class ProjectError(Exception):
    pass


def _git(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None, data: bytes | None = None) -> bytes:
    done = subprocess.run(["git", *SAFE, *args], cwd=cwd, input=data, capture_output=True,
                          env={**os.environ, **(env or {})})
    if done.returncode != 0:
        raise ProjectError(f"git {' '.join(args[:2])}: {done.stderr.decode(errors='replace').strip()[:500]}")
    return done.stdout


def resolve(path: Path) -> tuple[Path, str]:
    """The repository root that holds ``path`` and its last commit."""
    try:
        root = Path(_git("rev-parse", "--show-toplevel", cwd=path).decode().strip())
    except (ProjectError, FileNotFoundError, NotADirectoryError):
        raise ProjectError(f"{path} is not in a git repository. Run git init and commit, then start again.") from None
    try:
        base = _git("rev-parse", "--verify", "HEAD", cwd=root).decode().strip()
    except ProjectError:
        raise ProjectError(f"{root} has no commit yet. Commit once, then start again.") from None
    return root.resolve(), base


def prepare(root: Path, base: str, patches: Sequence[bytes], dest: Path, *, cache: Path) -> None:
    """Fill ``dest`` with the files of ``base``, then apply ``patches`` in order. ``cache`` keeps the base."""
    if not cache.exists():
        tmp = Path(tempfile.mkdtemp(prefix=".base-", dir=cache.parent if cache.parent.exists() else None))
        cache.parent.mkdir(parents=True, exist_ok=True)
        archive = _git("archive", "--format=tar", base, cwd=root)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter="data")
        try:
            os.replace(tmp, cache)
        except OSError:  # another process filled it first
            shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(cache, dest, symlinks=True)
    for patch in patches:
        if patch:  # outside any repository, so git applies paths from dest and nothing above it
            _git("apply", "--binary", "--whitespace=nowarn", "-", cwd=dest, data=patch,
                 env={"GIT_CEILING_DIRECTORIES": str(dest.parent)})


def apply(dest: Path, patch: bytes) -> bool:
    """Apply ``patch`` to the copy in ``dest`` whole, or leave it as it was when the patch no longer fits."""
    try:
        _git("apply", "--binary", "--whitespace=nowarn", "-", cwd=dest, data=patch,
             env={"GIT_CEILING_DIRECTORIES": str(dest.parent)})
    except ProjectError:
        return False
    return True


class Tracker:
    """Records what a step changed in its copy, from a git directory the agent cannot reach."""

    def __init__(self, git_dir: Path, work: Path) -> None:
        self.env = {"GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(work)}
        self.work = work
        _git("init", "-q", cwd=work, env=self.env)
        _git("add", "-A", cwd=work, env=self.env)
        _git("commit", "-q", "--allow-empty", "--no-verify", "-m", "before", cwd=work, env=self.env)

    def exclude(self, paths: Sequence[str]) -> None:
        """Leave ``paths``, folders relative to the copy, out of every change, such as linked dependencies."""
        info = Path(self.env["GIT_DIR"]) / "info"
        info.mkdir(exist_ok=True)
        with (info / "exclude").open("a") as f:
            f.writelines(f"/{p}/\n" for p in paths)

    def changes(self) -> tuple[bytes, list[str]]:
        """The patch from the copy as handed over to the copy now, and the paths it touches."""
        _git("add", "-A", cwd=self.work, env=self.env)
        patch = _git("diff", "--cached", "--binary", "--no-renames", "HEAD", cwd=self.work, env=self.env)
        # -z: names as written, since git otherwise quotes a name with accents or quotes and no glob matches it
        names = _git("diff", "--cached", "--name-only", "--no-renames", "-z", "HEAD", cwd=self.work, env=self.env)
        return patch, sorted(n for n in names.decode(errors="replace").split("\0") if n)


_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _unquote(quoted: str) -> str:
    """The first name git quoted in ``quoted``: C escapes, with octal for each byte past ASCII."""
    name, i = bytearray(), 1
    while quoted[i] != '"':
        if quoted[i] == "\\" and quoted[i + 1] in "01234567":
            name.append(int(quoted[i + 1:i + 4], 8))
            i += 4
        elif quoted[i] == "\\":
            name.append(_ESCAPES.get(quoted[i + 1], ord(quoted[i + 1])))
            i += 2
        else:
            name += quoted[i].encode()
            i += 1
    return name.decode(errors="replace")


def diffs(patch: str) -> dict[str, str]:
    """Each file's diff in a patch made with ``--no-renames``, by its path as written, even where git quoted it."""
    out: dict[str, str] = {}
    for d in re.split(r"(?m)^diff --git ", patch)[1:]:
        header = d.split("\n", 1)[0]
        # "a/<quoted>" "b/<quoted>", or a/<path> b/<path>: the same path twice since nothing is renamed
        path = _unquote(header) if header.startswith('"') else header[:(len(header) - 1) // 2]
        out[path.removeprefix("a/")] = f"diff --git {d}"
    return out


def branch(root: Path, base: str, name: str, patches: Sequence[tuple[str, bytes]]) -> str:
    """Commit each patch on ``name`` starting from ``base``, with a temporary index. Returns the last commit."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git("read-tree", base, cwd=root, env=env)
        parent = base
        for message, patch in patches:
            if patch:
                _git("apply", "--cached", "--binary", "--whitespace=nowarn", "-", cwd=root, env=env, data=patch)
            tree = _git("write-tree", cwd=root, env=env).decode().strip()
            parent = _git("commit-tree", tree, "-p", parent, "-m", message, cwd=root, env=env).decode().strip()
        _git("update-ref", f"refs/heads/{name}", parent, cwd=root)
    return parent


class NotMerged(ProjectError):
    """Why a merge, or its undo, did not land: the person merges or reverts by hand."""


def current_branch(root: Path) -> str | None:
    """The branch checked out at ``root``, or None when HEAD is detached."""
    done = subprocess.run(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=root, capture_output=True)
    return done.stdout.decode().strip() or None if done.returncode == 0 else None


def _tree(root: Path, base: str | None, ours: str, theirs: str) -> str:
    """The tree of a three-way merge made apart from any checkout, or NotMerged on a conflict."""
    args = ["git", *SAFE, "merge-tree", "--write-tree", "--no-messages", *(["--merge-base", base] if base else []),
            ours, theirs]
    done = subprocess.run(args, cwd=root, capture_output=True)
    if done.returncode == 1:
        raise NotMerged("it conflicts with what the branch has now")
    if done.returncode != 0:
        raise NotMerged(f"git merge-tree: {done.stderr.decode(errors='replace').strip()[:300]}")
    return done.stdout.decode().split("\n", 1)[0].strip()


def _checked_out(root: Path, branch: str) -> Path | None:
    """The worktree where ``branch`` is checked out, if any."""
    where: Path | None = None
    for line in _git("worktree", "list", "--porcelain", cwd=root).decode().splitlines():
        if line.startswith("worktree "):
            where = Path(line[len("worktree "):])
        elif line == f"branch refs/heads/{branch}":
            return where
    return None


def _land(root: Path, branch: str, old: str, new: str) -> None:
    """Move ``branch`` from ``old`` to ``new``. Where it is checked out, only by fast-forward over a clean checkout,
    so no uncommitted work is touched; elsewhere, only if it still points at ``old``."""
    where = _checked_out(root, branch)
    if where is None:
        _git("update-ref", f"refs/heads/{branch}", new, old, cwd=root)
        return
    if _git("status", "--porcelain", "--untracked-files=no", cwd=where).strip():
        raise NotMerged(f"{where} has changes not committed")
    done = subprocess.run(["git", *SAFE, "merge", "--ff-only", "-q", new], cwd=where, capture_output=True)
    if done.returncode != 0:
        raise NotMerged(f"the checkout at {where} could not move: "
                        f"{done.stderr.decode(errors='replace').strip()[:300]}")


def merge(root: Path, branch: str, commit: str, message: str, base: str | None = None) -> str:
    """Merge ``commit`` into ``branch`` with a merge commit made apart, and land it (``_land``). ``base`` is what the
    branch already has of this work, a correction merging only what changed since. Returns the merge commit."""
    head = _git("rev-parse", "--verify", f"refs/heads/{branch}", cwd=root).decode().strip()
    tree = _tree(root, base, head, commit)
    new = _git("commit-tree", tree, "-p", head, "-p", commit, "-m", message, cwd=root).decode().strip()
    _land(root, branch, head, new)
    return new


def revert(root: Path, branch: str, merged: str, message: str) -> str:
    """Undo the merge commit ``merged`` on ``branch`` with a new commit, landed as a merge is. Returns it."""
    head = _git("rev-parse", "--verify", f"refs/heads/{branch}", cwd=root).decode().strip()
    tree = _tree(root, merged, head, f"{merged}^1")
    new = _git("commit-tree", tree, "-p", head, "-m", message, cwd=root).decode().strip()
    _land(root, branch, head, new)
    return new


# Paths that trusted tools run or obey later, outside any box: agents' and editors' settings and instructions, git
# attributes and hooks, direnv, interpreters and package scripts, test and build entry points, dev containers, CI.
# ``may_change`` alone never lets them through.
FLOOR = ("**/.claude/**", "**/.codex/**", "**/.cursor/**", "**/.gemini/**", "**/.vscode/**", "**/.idea/**",
         "**/.zed/**", "**/.mcp.json", "**/CLAUDE.md", "**/AGENTS.md", "**/.cursorrules",
         "**/.git*", "**/.husky/**", "**/.pre-commit-config.yaml", "**/.envrc",
         "**/.venv/**", "**/venv/**", "**/package.json", "**/.npmrc", "**/.yarnrc*", "**/pyproject.toml",
         "**/setup.py", "**/setup.cfg", "**/sitecustomize.py", "**/usercustomize.py", "**/conftest.py",
         "**/Makefile", "**/GNUmakefile", "**/.devcontainer/**", ".gitlab-ci.yml", ".circleci/**", ".buildkite/**",
         "Jenkinsfile", "azure-pipelines.yml", ".travis.yml")


def _matches(path: str, glob: str) -> bool:
    return fnmatchcase(path, glob) or (glob.startswith("**/") and fnmatchcase(path, glob[3:]))


def allowed(paths: Sequence[str], globs: Sequence[str]) -> list[str]:
    """The paths no glob matches, so with no globs every path. ``**/`` also matches the top level, and a glob
    that starts with ``!`` keeps the paths it matches out even when another glob lets them in."""
    keep = [g for g in globs if not g.startswith("!")]
    out = [g[1:] for g in globs if g.startswith("!")]
    return [p for p in paths if not any(_matches(p, g) for g in keep) or any(_matches(p, g) for g in out)]


def floor(paths: Sequence[str]) -> list[str]:
    """The paths on the floor, in any case, since macOS and Windows file systems ignore it."""
    return [p for p in paths if any(_matches(p.lower(), g.lower()) for g in FLOOR)]

