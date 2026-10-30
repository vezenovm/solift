"""The package's boundary. `src/z3sol` imports only the standard library, itself and its declared dependencies, so an
install of the wheel has everything that the package imports. The dependencies are pinned, and no Python file names a
path in a home directory."""

import ast
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "z3sol"
# lib holds the Solidity dependencies as git submodules.
SKIPPED_DIRS = {".git", ".venv", "build", "lib"}
# The import name of each declared dependency, by its distribution name.
IMPORT_NAMES = {"cvc5": "cvc5", "halmos": "halmos", "yices-solver": "yices", "z3-solver": "z3"}
# The character classes keep this pattern's own text from matching it.
HOME_PATH = re.compile(r"/User[s]/|/hom[e]/")


def dependencies() -> list[str]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]


def imports(source: str, name: str) -> list[tuple[int, str]]:
    """The line and the top-level module of each absolute import."""
    found = []
    for node in ast.walk(ast.parse(source, name)):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name.split(".")[0]) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            found.append((node.lineno, node.module.split(".")[0]))
    return found


def python_files() -> list[pathlib.Path]:
    return sorted(path for path in ROOT.rglob("*.py") if not SKIPPED_DIRS & set(path.relative_to(ROOT).parts))


def test_the_package_imports_only_the_standard_library_itself_and_its_dependencies():
    allowed = {"z3sol"} | {IMPORT_NAMES[pin.partition("==")[0]] for pin in dependencies()}
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for line, module in imports(path.read_text(), path.name):
            if module not in allowed and module not in sys.stdlib_module_names:
                found.append(f"{path.relative_to(ROOT)}:{line}: imports {module}")
    assert found == []


def test_the_check_finds_an_import_of_anything_else():
    assert imports("import os\nfrom z3sol.ir import Model\nimport numpy.linalg\nfrom . import ops\n", "sample.py") == [
        (1, "os"),
        (2, "z3sol"),
        (3, "numpy"),
    ]


def test_no_python_file_names_a_path_in_a_home_directory():
    found = []
    for path in python_files():
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and HOME_PATH.search(node.value):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert found == []


def test_the_package_depends_only_on_pinned_versions():
    pins = dependencies() + (ROOT / "requirements-dev.txt").read_text().split()
    assert [pin for pin in pins if not re.fullmatch(r"[a-z0-9-]+==[0-9][0-9a-z.]*", pin)] == []
