"""Where a run writes, and how it builds a project without writing to it.

A workspace is a directory that one run owns: every path the run writes is inside it. A marker file shows that solift
made it, and `fresh` deletes only inside a marked workspace. A process holds an exclusive lock on each workspace it
opens until it exits, so two runs never share one and validate each other's generated files. A forked process holds
none of its parent's locks, and `fresh` does not delete a workspace that another process holds.

A project is the foundry project that holds the target. solift never writes to it. It builds an overlay root in the
workspace instead: symlinks to the project's configuration and dependencies (`link`), copies of the sources that forge
compiles as project files or that a mutant changes (`copy`), local files at paths that the project does not have
(`add`), such as the harness of a contract that the project does not test, and the generated files. Forge reads a
symlinked project file under its real path and then finds no AST for it, so project files are copied, not linked. A
copy keeps the links inside it, so each write into the overlay refuses a link and a path that resolves outside the
overlay. Forge also gives a remapping that it detects in a linked library the library's real path, so the overlay keeps
each remapping at the path that the project gives it (`_keep_remappings`). `guard` checks that nothing in the project
changed during the run.
"""

from __future__ import annotations

import fcntl
import os
import pathlib
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from solift import forge

MARKER = ".solift-workspace"
LOCK = ".solift-lock"
# The lock files this process holds, open for as long as it runs: the process id and the descriptor of each. A forked
# process inherits the descriptors, but not the ownership.
_HELD: dict[pathlib.Path, tuple[int, int]] = {}


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, path: pathlib.Path):
        self.path = path.resolve()
        if self.path.exists() and any(self.path.iterdir()) and not (self.path / MARKER).exists():
            raise WorkspaceError(f"{self.path} is not empty and is not a solift workspace")
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / MARKER).touch()
        _lock(self.path)

    def fresh(self, name: str) -> pathlib.Path:
        """An empty directory `name` in the workspace."""
        path = (self.path / name).resolve()
        if self.path not in path.parents:
            raise WorkspaceError(f"{name} is outside the workspace")
        if path.exists():
            _delete(path)
        path.mkdir(parents=True)
        return path

    def child(self, name: str) -> Workspace:
        """A fresh workspace inside this one, for one of several runs."""
        return Workspace(self.fresh(name))


@dataclass(frozen=True)
class Project:
    """`root` is None until the command line gives it, for a project outside this repo. `add` maps a project path that
    the project does not have to the local file that the overlay writes there. `generated` is the overlay directory for
    the generated files. It must be inside the profile's test directory, so that forge builds them.

    `Project.foundry()` leaves `link`, `copy` and `generated` as None, which `resolve` infers from the project's foundry
    configuration once the root is known. A field given as `()` stays empty."""

    root: pathlib.Path | None = None
    profile: str | None = None
    link: tuple[str, ...] | None = ()
    copy: tuple[str, ...] | None = ()
    add: dict[str, pathlib.Path] = field(default_factory=dict, hash=False)
    generated: str | None = "test/solift"

    def __post_init__(self):
        # The overlay links into the root, so a relative root would give links to themselves.
        if self.root is not None:
            object.__setattr__(self, "root", pathlib.Path(self.root).resolve())

    @classmethod
    def foundry(
        cls,
        root: pathlib.Path | None = None,
        profile: str | None = None,
        link: tuple[str, ...] | None = None,
        copy: tuple[str, ...] | None = None,
        add: dict[str, pathlib.Path] | None = None,
        generated: str | None = None,
    ) -> Project:
        """A conventional Foundry project, whose fields `resolve` infers: it links foundry.toml, remappings.txt and the
        library directories, copies the source directory, and writes the generated files to `<test>/solift`."""
        return cls(root, profile, link, copy, add or {}, generated)

    def resolve(self) -> Project:
        """The project with each inferred field set from `forge config --json` under its profile. `link` holds
        foundry.toml, remappings.txt, the library directories and the top directory of each remapping's target, such as
        `.deps` for `@lib/=.deps/lib/src/`, each that the project has and that holds no copied or generated path.
        `copy` holds the source directory if it exists."""
        if self.link is not None and self.copy is not None and self.generated is not None:
            return self
        config = forge.config(self.root, self.profile)
        src, test = str(pathlib.PurePosixPath(config["src"])), pathlib.PurePosixPath(config["test"])
        copy = self.copy if self.copy is not None else ((src,) if (self.root / src).is_dir() else ())
        generated = self.generated if self.generated is not None else f"{test}/solift"
        link = self.link
        if link is None:
            kept = [pathlib.PurePosixPath(p) for p in (*copy, generated)]
            remapped = [_remapped_top(r) for r in config.get("remappings", [])]
            link = tuple(
                p
                for p in dict.fromkeys(("foundry.toml", "remappings.txt", *config["libs"], *filter(None, remapped)))
                if (self.root / p).exists() and not any(_overlap(pathlib.PurePosixPath(p), k) for k in kept)
            )
        return Project(root=self.root, profile=self.profile, link=link, copy=copy, add=self.add, generated=generated)

    def overlay(self, workspace: Workspace, name: str = "root", replace: dict[str, str] | None = None) -> pathlib.Path:
        """Builds the overlay root, with `replace` (a project path to new content) applied to the copies. An entry under
        another entry would be built through it, through a link into the project, so the overlay refuses it before it
        writes anything."""
        entries = [pathlib.PurePosixPath(rel) for rel in (*self.link, *self.copy)]
        for entry in entries:
            for other in entries:
                if other != entry and other in entry.parents:
                    raise WorkspaceError(f"the overlay entry {entry} is under {other}")
        if len(set(entries)) != len(entries):
            raise WorkspaceError("an overlay entry is both linked and copied, or named twice")
        for rel in self.add:
            added = pathlib.PurePosixPath(rel)
            if added.is_absolute() or ".." in added.parts:
                raise WorkspaceError(f"the added file {rel} is not a relative path inside the project")
            if added in entries or any(pathlib.PurePosixPath(link) in added.parents for link in self.link):
                raise WorkspaceError(f"the added file {rel} is an overlay entry or under a linked one")
            if os.path.lexists(self.root / rel):
                raise WorkspaceError(f"the added file {rel} is in the project, which the overlay does not change")
        root = workspace.fresh(name)
        for rel in self.link:
            target = root / rel
            _inside(root, target.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(self.root / rel)
        for rel in self.copy:
            source, target = self.root / rel, root / rel
            _inside(root, target.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target)
        for rel, source in self.add.items():
            target = root / rel
            _inside(root, target.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            _write(root, target, pathlib.Path(source).read_text())
        for rel, content in (replace or {}).items():
            if not any(rel == c or rel.startswith(c.rstrip("/") + "/") for c in self.copy):
                raise WorkspaceError(f"{rel} is not under a copied path, so the overlay cannot replace it")
            _write(root, root / rel, content)
        _inside(root, root / self.generated)
        (root / self.generated).mkdir(parents=True, exist_ok=True)
        _keep_remappings(root, self.root, self.profile)
        return root

    def write(self, root: pathlib.Path, name: str, text: str) -> None:
        """Writes a generated file into the overlay's directory for them."""
        _write(root, root / self.generated / name, text)

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
        # The top's .git, and each .git directory of a repository inside the project, such as a checkout of solift,
        # whose index `git status` rewrites when a run records its version. A submodule's .git file is still watched.
        command = ["find", str(self.top), "(", "-path", str(self.top / ".git"), "-o", "-type", "d", "-name", ".git"]
        for path in self.exclude:
            command += ["-o", "-path", str(path)]
        command += [")", "-prune", "-o", "-newer", str(self.stamp), "-print"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        found += [f"{line} changed during the run" for line in result.stdout.splitlines() if line]
        return found


def _keep_remappings(root: pathlib.Path, project: pathlib.Path, profile: str | None) -> None:
    """Keeps each remapping that forge detects in a linked library inside the overlay. Some versions of forge follow
    the overlay's links into the project and give such a remapping, as `@x/=lib/a/lib/x/` from lib/a's own
    foundry.toml, the library's real path, while a remapping that the configuration states stays relative. A file that
    both reach would then be two source units, and its types two types. A detected remapping with an absolute path that
    the overlay reaches through a link is written to the overlay's remappings.txt with the overlay's path, which takes
    the place of the detected one of the same name. A remapping that the configuration or the environment states stays
    as it is."""
    remappings = forge.config(root, profile).get("remappings", [])
    stated = set(forge.config(root, profile, detect=False).get("remappings", []))
    kept = [r if r in stated else _relative(r, root, project) for r in remappings]
    if kept == remappings:
        return
    path = root / "remappings.txt"
    if path.is_symlink():
        path.unlink()
    _write(root, path, "".join(f"{r}\n" for r in kept))
    # A detected remapping whose context was rewritten keeps its own key, and stays beside the new one. Its absolute
    # context names no source unit of the overlay unless a stated remapping has an absolute path too, as in the project.
    taken = set(forge.config(root, profile).get("remappings", []))
    if not set(kept) <= taken <= set(kept) | set(remappings):
        raise WorkspaceError(f"forge does not take the remappings of the overlay's remappings.txt: {', '.join(kept)}")


def _relative(remapping: str, root: pathlib.Path, project: pathlib.Path) -> str:
    """The remapping `[context:]name=target` with its context and target at their paths in the overlay, where they are
    absolute paths inside the project that the overlay reaches through its links. A path keeps its spelling, `..` too:
    solc does not normalize a remapping's target, so another spelling would name other source units."""

    def relative(path: str) -> str:
        at, top = pathlib.PurePosixPath(path), pathlib.PurePosixPath(project)
        normal = pathlib.PurePosixPath(os.path.normpath(path)) if path else at
        if not at.is_absolute() or not at.is_relative_to(top) or not normal.is_relative_to(top) or normal == top:
            return path
        rel = at.relative_to(top)
        if not os.path.exists(root / rel) or (root / rel).resolve() != pathlib.Path(path).resolve():
            return path
        return f"{rel}/" if path.endswith("/") else str(rel)

    left, _, target = remapping.partition("=")
    context, colon, name = left.partition(":")
    if not colon:
        return f"{left}={relative(target)}"
    return f"{relative(context)}:{name}={relative(target)}"


def _remapped_top(remapping: str) -> str | None:
    """The first part of a remapping's target, `.deps` for `@lib/=.deps/lib/src/` or `ctx:@lib/=.deps/lib/src/`, when
    it is a relative path inside the project."""
    target = pathlib.PurePosixPath(remapping.partition("=")[2])
    if not target.parts or target.is_absolute() or target.parts[0] in (".", ".."):
        return None
    return target.parts[0]


def _overlap(a: pathlib.PurePosixPath, b: pathlib.PurePosixPath) -> bool:
    return a == b or a in b.parents or b in a.parents


def _lock(path: pathlib.Path) -> None:
    lock = path / LOCK
    if _holds(lock):
        return
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise WorkspaceError(f"{path} is in use by another process") from None
    _HELD[lock] = (os.getpid(), fd)


def _holds(lock: pathlib.Path) -> bool:
    """Whether this process holds the lock on the file that is at `lock` now."""
    if lock not in _HELD or _HELD[lock][0] != os.getpid():
        return False
    try:
        return os.fstat(_HELD[lock][1]).st_ino == os.stat(lock).st_ino
    except FileNotFoundError:
        return False


def _delete(path: pathlib.Path) -> None:
    """Deletes `path`, holding the lock of each workspace inside it while it does. Refuses, before it deletes anything,
    a workspace that another process holds. This process no longer holds the workspaces that it deletes."""
    locks = [pathlib.Path(top) / LOCK for top, _, files in os.walk(path) if LOCK in files]
    taken = []
    try:
        for lock in locks:
            if not _holds(lock):
                fd = os.open(lock, os.O_RDWR)
                taken.append(fd)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise WorkspaceError(f"{lock.parent} is in use by another process") from None
        taken += [_HELD.pop(lock)[1] for lock in locks if _holds(lock)]
        shutil.rmtree(path)
    finally:
        for fd in taken:
            os.close(fd)


def _inside(root: pathlib.Path, path: pathlib.Path) -> None:
    """Refuses a path that resolves outside the overlay, as one under a linked directory does."""
    if path.resolve() != root.resolve() and root.resolve() not in path.resolve().parents:
        raise WorkspaceError(f"{path.relative_to(root)} is outside the overlay: it resolves to {path.resolve()}")


def _write(root: pathlib.Path, path: pathlib.Path, text: str) -> None:
    if path.is_symlink():
        raise WorkspaceError(f"{path.relative_to(root)} is a link, so writing it would write {path.resolve()}")
    _inside(root, path)
    path.write_text(text)


def _top(root: pathlib.Path) -> pathlib.Path | None:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return pathlib.Path(result.stdout.strip()) if result.returncode == 0 else None
