"""Where a run writes, and how it builds a project without writing to it.

A workspace is a directory that one run owns: every path the run writes is inside it. A marker file shows that z3sol
made it, and `fresh` deletes only inside a marked workspace.

A project is the foundry project that holds the target. z3sol never writes to it. It builds an overlay root in the
workspace instead: symlinks to the project's configuration and dependencies (`link`), copies of the sources that forge
compiles as project files or that a mutant changes (`copy`), and the generated files. Forge reads a symlinked project
file under its real path and then finds no AST for it, so project files are copied, not linked. `guard` checks that
nothing in the project changed during the run.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import time
from dataclasses import dataclass

MARKER = ".z3sol-workspace"


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, path: pathlib.Path):
        self.path = path.resolve()
        if self.path.exists() and any(self.path.iterdir()) and not (self.path / MARKER).exists():
            raise WorkspaceError(f"{self.path} is not empty and is not a z3sol workspace")
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / MARKER).touch()

    def fresh(self, name: str) -> pathlib.Path:
        """An empty directory `name` in the workspace."""
        path = (self.path / name).resolve()
        if self.path not in path.parents:
            raise WorkspaceError(f"{name} is outside the workspace")
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        return path

    def child(self, name: str) -> Workspace:
        """A fresh workspace inside this one, for one of several runs."""
        return Workspace(self.fresh(name))


@dataclass(frozen=True)
class Project:
    """`root` is None until the command line gives it, for a project outside this repo. `generated` is the overlay
    directory for the generated files. It must be inside the profile's test directory, so that forge builds them."""

    root: pathlib.Path | None = None
    profile: str | None = None
    link: tuple[str, ...] = ()
    copy: tuple[str, ...] = ()
    generated: str = "test/z3sol"

    def overlay(self, workspace: Workspace, name: str = "root", replace: dict[str, str] | None = None) -> pathlib.Path:
        """Builds the overlay root, with `replace` (a project path to new content) applied to the copies."""
        root = workspace.fresh(name)
        for rel in self.link:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(self.root / rel)
        for rel in self.copy:
            source, target = self.root / rel, root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target)
        for rel, content in (replace or {}).items():
            if not any(rel == c or rel.startswith(c.rstrip("/") + "/") for c in self.copy):
                raise WorkspaceError(f"{rel} is not under a copied path, so the overlay cannot replace it")
            (root / rel).write_text(content)
        (root / self.generated).mkdir(parents=True, exist_ok=True)
        return root

    def guard(self, workspace: Workspace, exclude=()) -> Guard:
        """A guard of the project, outside the workspace and the `exclude` paths that the run writes to on purpose."""
        return Guard(self.root, workspace.path / "guard.stamp", exclude=(workspace.path, *exclude))


class Guard:
    """Records the git status (ignored files included) of the repository that holds `root`, and touches `stamp`.
    `changes` lists each file newer than the stamp and any change of the status, outside the `exclude` paths."""

    def __init__(self, root: pathlib.Path, stamp: pathlib.Path, exclude=()):
        self.top = _top(root) or root.resolve()
        self.exclude = tuple(p.resolve() for p in exclude)
        self.status = self._status()
        self.stamp = stamp
        stamp.touch()
        time.sleep(1.1)  # some file systems keep file times to the second

    def _excluded(self, path: pathlib.Path) -> bool:
        path = path.resolve()
        return any(path == e or e in path.parents for e in self.exclude)

    def _status(self) -> list[str]:
        result = subprocess.run(
            ["git", "-C", str(self.top), "status", "--porcelain", "--ignored"], capture_output=True, text=True
        )
        return [line for line in result.stdout.splitlines() if not self._excluded(self.top / line[3:].rstrip("/"))]

    def changes(self) -> list[str]:
        found = []
        if self._status() != self.status:
            found.append(f"the git status of {self.top} changed during the run")
        pruned = [str(self.top / ".git"), *(str(e) for e in self.exclude)]
        command = ["find", str(self.top), "("]
        for i, path in enumerate(pruned):
            command += (["-o"] if i else []) + ["-path", path]
        command += [")", "-prune", "-o", "-newer", str(self.stamp), "-print"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        found += [f"{line} changed during the run" for line in result.stdout.splitlines() if line]
        return found


def _top(root: pathlib.Path) -> pathlib.Path | None:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return pathlib.Path(result.stdout.strip()) if result.returncode == 0 else None
