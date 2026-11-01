import os
import pathlib
import re
import subprocess
import sys

import pytest

from solift import forge
from solift.workspace import MARKER, Guard, Project, Workspace, WorkspaceError


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
    with pytest.raises(WorkspaceError, match="is not a solift workspace"):
        Workspace(tmp_path / "data")


def test_the_overlay_links_and_copies_and_replaces_only_copies(tmp_path):
    project = Project(git_project(tmp_path / "project"), link=("foundry.toml",), copy=("src",), generated="test/gen")
    workspace = Workspace(tmp_path / "work")
    root = project.overlay(workspace, replace={"src/A.sol": "contract B {}\n"})
    assert (root / "foundry.toml").is_symlink()
    assert (root / "src" / "A.sol").read_text() == "contract B {}\n"
    assert (project.root / "src" / "A.sol").read_text() == "contract A {}\n"
    assert (root / "test" / "gen").is_dir()
    assert not os.path.lexists(root / "remappings.txt")
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


def test_the_guard_ignores_the_git_directory_of_a_repository_inside_the_project(tmp_path):
    root = git_project(tmp_path / "project")
    nested = root / "tools" / "checkout"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    (nested / "README").write_text("a checkout, as of solift\n")
    (root / "lib" / "b").mkdir(parents=True)
    (root / "lib" / "b" / ".git").write_text("gitdir: ../../.git/modules/b\n")
    workspace = Workspace(tmp_path / "work")
    guard = Guard(root, workspace.path / "stamp")
    # What `git status` in the checkout writes, when a run records the checkout's version.
    subprocess.run(["git", "-C", str(nested), "add", "-A"], check=True)
    assert guard.changes() == []
    (nested / "README").write_text("changed\n")
    (root / "lib" / "b" / ".git").write_text("gitdir: elsewhere\n")
    assert sorted(guard.changes()) == [
        f"{root}/lib/b/.git changed during the run",
        f"{nested}/README changed during the run",
    ]


LIBRARY = {
    "lib/a/foundry.toml": '[profile.default]\nsrc = "src"\nlibs = ["lib"]\nremappings = ["@x/=lib/x/"]\n',
    "lib/a/lib/x/src/I.sol": "pragma solidity ^0.8.20;\n\ninterface I {\n  function f() external;\n}\n",
    "lib/a/src/A.sol": (
        'pragma solidity ^0.8.20;\n\nimport {I} from "@x/src/I.sol";\n\n'
        "contract A {\n  function use(I) external {}\n}\n"
    ),
    # I through the project's remapping @ax/, and A, which imports I through @x/, the remapping that forge detects in
    # lib/a's foundry.toml: both name lib/a/lib/x/src/I.sol.
    "src/Main.sol": (
        'pragma solidity ^0.8.20;\n\nimport {I} from "@ax/I.sol";\nimport {A} from "a/A.sol";\n\n'
        "contract Main {\n  function call(A _a) external {\n    _a.use(I(address(0)));\n  }\n}\n"
    ),
}


@pytest.mark.parametrize("remapped_in", ["foundry.toml", "remappings.txt", "an added foundry.toml"])
def test_the_overlay_keeps_the_remappings_that_forge_detects_in_a_linked_library_inside_the_overlay(
    tmp_path, remapped_in
):
    root = tmp_path / "project"
    files = {**LIBRARY, "foundry.toml": '[profile.default]\nsrc = "src"\nlibs = ["lib"]\n'}
    added = {}
    if remapped_in == "foundry.toml":
        files["foundry.toml"] += 'remappings = ["@ax/=lib/a/lib/x/src/"]\n'
    elif remapped_in == "remappings.txt":
        files["remappings.txt"] = "@ax/=lib/a/lib/x/src/\n"
    else:
        # Only the overlay has a configuration, which the project lacks.
        added["foundry.toml"] = tmp_path / "added.toml"
        added["foundry.toml"].write_text(files.pop("foundry.toml") + 'remappings = ["@ax/=lib/a/lib/x/src/"]\n')
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    project = Project.foundry(root=root, add=added).resolve()
    workspace = Workspace(tmp_path / "work")
    overlay = project.overlay(workspace)
    assert (overlay / "lib").is_symlink()
    # Some versions of forge give the detected @x/ the library's real path, and the overlay's remappings.txt the
    # relative one; others give it the relative path already.
    remappings = forge.config(overlay)["remappings"]
    assert {"@ax/=lib/a/lib/x/src/", "@x/=lib/a/lib/x/"} <= set(remappings)
    assert not any(str(project.root) in r for r in remappings)
    infos = forge.build(overlay, workspace.fresh("out"), workspace.fresh("cache"), allow_paths=(root,))
    assert {path for info in infos for path in info["output"]["sources"]} == {
        "src/Main.sol",
        "lib/a/src/A.sol",
        "lib/a/lib/x/src/I.sol",
    }
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_the_overlay_writes_a_detected_remapping_that_it_reaches_through_a_link_with_its_own_path(
    tmp_path, monkeypatch
):
    root = git_project(tmp_path / "project")
    (root / "lib" / "a" / "lib" / "w").mkdir(parents=True)
    (root / "remappings.txt").write_text("@y/=lib/a/\n")
    project = Project(root, link=("foundry.toml", "remappings.txt", "lib"), copy=("src",), generated="test/gen")
    top = project.root
    # Forge, in the overlay: the detected @x/, @t/ with a .. that it keeps, and the context of @w/ through the link,
    # and @u/ at a path that the overlay lacks; then the stated @y/ of remappings.txt, and @v/ and @z/ with the
    # absolute paths that the configuration states, also with a .. outside the project.
    context = f"{top}/lib/a/:@w/={top}/lib/a/lib/w/"
    detected = [f"@x/={top}/lib/a/", f"@t/={top}/lib/a/lib/../lib/w/", context, f"@u/={top}/missing/"]
    stated = ["@y/=lib/a/", f"@v/={top}/lib/a/", f"@z/={top}/../elsewhere/"]
    kept = ["@x/=lib/a/", "@t/=lib/a/lib/../lib/w/", "lib/a/:@w/=lib/a/lib/w/", f"@u/={top}/missing/", *stated]
    ignored = [False]

    def config(at, profile=None, detect=True):
        file = pathlib.Path(at) / "remappings.txt"
        if file.is_symlink() or ignored[0]:
            return {"remappings": [*detected, *stated] if detect else stated}
        # Forge still detects the remapping of @w/ at its absolute context, a key of its own.
        return {"remappings": [*file.read_text().splitlines(), context]}

    monkeypatch.setattr(forge, "config", config)
    overlay = project.overlay(Workspace(tmp_path / "work"))
    assert not (overlay / "remappings.txt").is_symlink()
    assert (overlay / "remappings.txt").read_text().splitlines() == kept
    assert (root / "remappings.txt").read_text() == "@y/=lib/a/\n"
    ignored[0] = True
    with pytest.raises(WorkspaceError, match="forge does not take the remappings of the overlay's remappings.txt"):
        project.overlay(Workspace(tmp_path / "work2"))


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
        [sys.executable, "-c", "import sys, pathlib; from solift.workspace import Workspace; "
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
        [sys.executable, "-c", "import sys, pathlib; from solift.workspace import Workspace; "
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


def test_the_overlay_adds_a_local_file_where_the_project_has_none(tmp_path):
    harness = tmp_path / "Harness.sol"
    harness.write_text("contract Harness {}\n")
    project = Project(
        git_project(tmp_path / "project"),
        link=("foundry.toml",),
        copy=("src",),
        add={"test/Harness.sol": harness, "src/Added.sol": harness},
    )
    root = project.overlay(Workspace(tmp_path / "work"))
    assert (root / "test" / "Harness.sol").read_text() == "contract Harness {}\n"
    assert (root / "src" / "Added.sol").read_text() == "contract Harness {}\n"
    assert not (tmp_path / "project" / "test").exists() and not (tmp_path / "project" / "src" / "Added.sol").exists()


@pytest.mark.parametrize(
    "path, message",
    [
        ("src/A.sol", "the added file src/A.sol is in the project"),
        ("foundry.toml/x.sol", "the added file foundry.toml/x.sol is an overlay entry or under a linked one"),
        ("src", "the added file src is an overlay entry or under a linked one"),
        ("../x.sol", "the added file ../x.sol is not a relative path inside the project"),
        ("/tmp/x.sol", "the added file /tmp/x.sol is not a relative path inside the project"),
    ],
)
def test_an_added_file_that_the_project_has_or_a_link_holds_is_refused_before_any_write(tmp_path, path, message):
    harness = tmp_path / "Harness.sol"
    harness.write_text("contract Harness {}\n")
    project = Project(git_project(tmp_path / "project"), link=("foundry.toml",), copy=("src",), add={path: harness})
    workspace = Workspace(tmp_path / "work")
    with pytest.raises(WorkspaceError, match=re.escape(message)):
        project.overlay(workspace)
    assert not (workspace.path / "root").exists()
