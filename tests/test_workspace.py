import os
import subprocess
import sys

import pytest

from z3sol.workspace import MARKER, Guard, Project, Workspace, WorkspaceError


def git_project(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "src").mkdir()
    (path / "src" / "A.sol").write_text("contract A {}\n")
    (path / "foundry.toml").write_text("[profile.default]\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    return path


def test_a_workspace_deletes_only_inside_itself(tmp_path):
    workspace = Workspace(tmp_path / "work")
    (workspace.fresh("out") / "file").write_text("x")
    assert not any(workspace.fresh("out").iterdir())
    with pytest.raises(WorkspaceError, match="outside the workspace"):
        workspace.fresh("../elsewhere")


def test_a_directory_with_other_files_is_not_taken_as_a_workspace(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "notes.txt").write_text("keep")
    with pytest.raises(WorkspaceError, match="is not a z3sol workspace"):
        Workspace(tmp_path / "data")


def test_the_overlay_links_and_copies_and_replaces_only_copies(tmp_path):
    project = Project(git_project(tmp_path / "project"), link=("foundry.toml",), copy=("src",), generated="test/gen")
    workspace = Workspace(tmp_path / "work")
    root = project.overlay(workspace, replace={"src/A.sol": "contract B {}\n"})
    assert (root / "foundry.toml").is_symlink()
    assert (root / "src" / "A.sol").read_text() == "contract B {}\n"
    assert (project.root / "src" / "A.sol").read_text() == "contract A {}\n"
    assert (root / "test" / "gen").is_dir()
    with pytest.raises(WorkspaceError, match="not under a copied path"):
        project.overlay(workspace, replace={"foundry.toml": ""})


def test_the_guard_finds_a_file_that_changed_in_the_project_and_ignores_the_workspace(tmp_path):
    root = git_project(tmp_path / "project")
    workspace = Workspace(root / "build" / "work")
    guard = Project(root).guard(workspace)
    (workspace.fresh("out") / "artifact.json").write_text("{}")
    assert guard.changes() == []
    (root / "src" / "A.sol").write_text("contract A { }\n")
    (root / "cache.json").write_text("{}")
    changes = guard.changes()
    assert f"the git status of {root} changed during the run" in changes
    assert f"{root}/src/A.sol changed during the run" in changes
    assert f"{root}/cache.json changed during the run" in changes


def test_the_guard_finds_an_ignored_build_output(tmp_path):
    root = git_project(tmp_path / "project")
    (root / ".gitignore").write_text("out/\n")
    workspace = Workspace(tmp_path / "work")
    guard = Guard(root, workspace.path / "stamp")
    (root / "out").mkdir()
    (root / "out" / "A.json").write_text("{}")
    assert f"{root}/out/A.json changed during the run" in guard.changes()


def test_no_write_into_the_overlay_follows_a_link_into_the_project(tmp_path):
    root = git_project(tmp_path / "project")
    (root / "lib").mkdir()
    (root / "lib" / "B.sol").write_text("contract B {}\n")
    (root / "src" / "B.sol").symlink_to(root / "lib" / "B.sol")
    workspace = Workspace(tmp_path / "work")
    project = Project(root, link=("lib",), copy=("src",), generated="test/gen")
    with pytest.raises(WorkspaceError, match="src/B.sol is a link"):
        project.overlay(workspace, replace={"src/B.sol": "contract C {}\n"})
    assert (root / "lib" / "B.sol").read_text() == "contract B {}\n"
    with pytest.raises(WorkspaceError, match="lib/gen is outside the overlay"):
        Project(root, link=("lib",), copy=("src",), generated="lib/gen").overlay(workspace)
    assert not (root / "lib" / "gen").exists()
    overlay = project.overlay(workspace)
    (root / "test").mkdir()
    (overlay / "test" / "gen" / "M.sol").symlink_to(root / "test" / "M.sol")
    with pytest.raises(WorkspaceError, match="M.sol is a link"):
        project.write(overlay, "M.sol", "contract M {}\n")
    assert not (root / "test" / "M.sol").exists()
    project.write(overlay, "N.sol", "contract N {}\n")
    assert (overlay / "test" / "gen" / "N.sol").read_text() == "contract N {}\n"


def test_a_workspace_belongs_to_one_process_at_a_time(tmp_path):
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys, pathlib; from z3sol.workspace import Workspace; "
         f"w = Workspace(pathlib.Path({str(tmp_path / 'work')!r})); print('held', flush=True); sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(WorkspaceError, match="is in use by another process"):
            Workspace(tmp_path / "work")
    finally:
        holder.stdin.close()
        holder.wait()
    Workspace(tmp_path / "work").fresh("out")
    assert Workspace(tmp_path / "work").path == (tmp_path / "work").resolve()


def test_a_workspace_that_another_process_holds_is_not_deleted_from_its_parent(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / MARKER).touch()
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys, pathlib; from z3sol.workspace import Workspace; "
         f"w = Workspace(pathlib.Path({str(tmp_path / 'work' / 'specs')!r})); (w.path / 'mine').touch(); "
         "print('held', flush=True); sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        assert holder.stdout.readline().strip() == "held"
        parent = Workspace(tmp_path / "work")
        with pytest.raises(WorkspaceError, match="specs is in use by another process"):
            parent.child("specs")
        assert (tmp_path / "work" / "specs" / "mine").exists()
    finally:
        holder.stdin.close()
        holder.wait()
    assert not (parent.child("specs").path / "mine").exists()


def test_a_forked_process_does_not_inherit_a_workspace(tmp_path):
    Workspace(tmp_path / "work")
    pid = os.fork()
    if pid == 0:
        try:
            Workspace(tmp_path / "work")
        except WorkspaceError:
            os._exit(0)
        os._exit(1)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0, "the forked process opened the workspace that its parent holds"


def test_an_overlay_entry_under_another_entry_is_refused_before_any_write(tmp_path):
    root = git_project(tmp_path / "project")
    (root / "lib").mkdir()
    (root / "lib" / "new").mkdir()
    (root / "lib" / "new" / "entry").write_text("x")
    workspace = Workspace(tmp_path / "work")
    with pytest.raises(WorkspaceError, match="lib/new/entry is under lib"):
        Project(root, link=("lib", "lib/new/entry"), generated="test/gen").overlay(workspace)
    with pytest.raises(WorkspaceError, match="src/A.sol is under src"):
        Project(root, link=("src/A.sol",), copy=("src",), generated="test/gen").overlay(workspace)
    assert sorted(p.name for p in (root / "lib" / "new").iterdir()) == ["entry"]
