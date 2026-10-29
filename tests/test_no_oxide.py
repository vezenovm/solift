"""This repo must not depend on Oxide. Each Python file imports only the standard library, this repo's own packages and
the pinned dependencies. It does not touch sys.path, does not load a module by name or path, and names no path outside
the repo. CI also runs without an Oxide checkout, so a dependency that slips past these rules fails there."""

import ast
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
# lib holds the Solidity dependencies as git submodules.
SKIPPED_DIRS = {".git", ".venv", "build", "lib"}
OWN_PACKAGES = {"z3sol", "tests", "acceptance", "examples"}
# Import names of the pinned dependencies that the code imports.
DEPENDENCY_IMPORTS = {"z3", "pytest"}
LOADERS = {"__import__", "import_module", "spec_from_file_location", "SourceFileLoader", "run_path", "run_module"}
# The character classes keep this pattern's own text from matching it.
OUTSIDE_PATH = re.compile(r"/User[s]/|/hom[e]/|aztec-labs-en[g]")


def violations(source: str, name: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source, name)):
        line = getattr(node, "lineno", 0)
        where = f"{name}:{line}"
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots = [node.module.split(".")[0]]
            if node.module == "sys" and any(alias.name == "path" for alias in node.names):
                found.append((line, f"{where}: imports sys.path"))
        else:
            roots = []
        for root in roots:
            if root not in OWN_PACKAGES | DEPENDENCY_IMPORTS and root not in sys.stdlib_module_names:
                found.append(
                    (line, f"{where}: imports {root}, which is not the standard library, this repo or a dependency")
                )
        if isinstance(node, ast.Attribute) and node.attr == "path":
            if isinstance(node.value, ast.Name) and node.value.id == "sys":
                found.append((line, f"{where}: uses sys.path"))
        if isinstance(node, ast.Call):
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if callee in LOADERS:
                found.append((line, f"{where}: loads a module by name or path ({callee})"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and OUTSIDE_PATH.search(node.value):
            found.append((line, f"{where}: names a path outside the repo"))
    return [message for _, message in sorted(found, key=lambda item: item[0])]


def python_files() -> list[pathlib.Path]:
    return sorted(path for path in ROOT.rglob("*.py") if not SKIPPED_DIRS & set(path.relative_to(ROOT).parts))


def test_no_python_file_depends_on_oxide():
    found = []
    for path in python_files():
        found += violations(path.read_text(), str(path.relative_to(ROOT)))
    assert found == []


def test_the_check_refuses_each_way_to_reach_oxide():
    # The path literal is split so that this file does not trip the check it tests.
    source = "\n".join(
        [
            "import sys",
            "import importlib",
            "from sys import path",
            "sys.path.insert(0, '../oxide/formulas')",
            "from subsidies import deposit_quote",
            "import dsl",
            "importlib.import_module('subsidies')",
            "OXIDE = '/" + "home/me/oxide'",
        ]
    )
    found = violations(source, "sample.py")
    assert found == [
        "sample.py:3: imports sys.path",
        "sample.py:4: uses sys.path",
        "sample.py:5: imports subsidies, which is not the standard library, this repo or a dependency",
        "sample.py:6: imports dsl, which is not the standard library, this repo or a dependency",
        "sample.py:7: loads a module by name or path (import_module)",
        "sample.py:8: names a path outside the repo",
    ]


def test_the_package_depends_only_on_pinned_versions():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pins = project["dependencies"] + (ROOT / "requirements-dev.txt").read_text().split()
    unpinned = [pin for pin in pins if not re.fullmatch(r"[a-z0-9-]+==[0-9][0-9a-z.]*", pin)]
    assert unpinned == []
    assert not [pin for pin in pins if "oxide" in pin]
