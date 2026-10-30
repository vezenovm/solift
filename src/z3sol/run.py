"""A run: extract each spec's model, prove its theorems, check its vectors, link it to the bytecode, and record each
result with the versions it rests on.

The specs of one run share a project. The run builds it once, runs the vectors of every model in one forge test, and
links every model in one Halmos run, with one test of the operator-table rows and lemmas that the models use. Each stage
runs even when an earlier one fails, except that a spec without a model has nothing to check, so one run shows every
failure. The run fails when a result is not the expected one, and when the project changed during the run.

The record is JSON. `schema` numbers its format.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import importlib.metadata
import json
import pathlib
import platform
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field

import z3

from z3sol import forge, solvers
from z3sol.extract import ExtractionError, extract
from z3sol.halmos import HalmosEngine
from z3sol.link import Link, LinkError, generate, operators_test
from z3sol.solidity import names
from z3sol.theorem import Terms, TheoremError, prove, theorem
from z3sol.vectors import VectorError, sample, vectors_source
from z3sol.workspace import Project, Workspace

MATH_PATH = "utils/math/Math.sol"
SCHEMA = 2
SHARED_OPERATORS = "Z3sol"


@dataclass
class Spec:
    """What a run checks: the target function, its read map, its link, and its theorems. `impossible` names the
    outcomes (pays, zero, reverts) that no input reaches, which the vectors then do not require. `stem` names the
    generated contracts, for two targets whose contract and function names give the same names, such as `_quote` and
    `quote` (solidity.names)."""

    name: str
    target: str
    reads: dict[str, str]
    link: Link
    project: Project
    theorems: list = field(default_factory=list)
    impossible: tuple[str, ...] = ()
    stem: str = ""

    def theorem(self, **options):
        def wrap(goal):
            declared = theorem(**options)(goal)
            if any(t.id == declared.id for t in self.theorems):
                raise TheoremError(f"two theorems are named {declared.id}")
            self.theorems.append(declared)
            return declared

        return wrap


@dataclass
class _Checked:
    """One spec in a run: its record, and its model and generated files once it has them."""

    spec: Spec
    record: dict
    model: object = None
    generated: object = None
    vectors: list | None = None


def run(
    specs: Spec | Sequence[Spec],
    workspace: Workspace,
    replace: dict[str, str] | None = None,
    link: bool = True,
    vectors: bool = True,
    emit: pathlib.Path | None = None,
    only: tuple[str, ...] | None = None,
    engine: HalmosEngine | None = None,
) -> dict:
    """Runs the specs and returns the record. `replace` gives new content for project files, such as a mutant. Without
    `link` Halmos does not run, and without `vectors` neither do the vectors, and the record says so. `emit` is a
    directory to write the models and their link tests to, for review. `only` names the theorems to prove, when a run
    needs only some of them, as a mutant does."""
    specs = [specs] if isinstance(specs, Spec) else list(specs)
    if not specs or any(spec.project != specs[0].project for spec in specs):
        raise ValueError("a run needs specs, and its specs share one project")
    if len({spec.name for spec in specs}) != len(specs):
        raise ValueError("two specs of the run have the same name")
    engine = engine or HalmosEngine()
    project = specs[0].project
    record = {
        "schema": SCHEMA,
        "started": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "project": _project(project.root),
        "replaced": sorted(replace or {}),
        "versions": _tool_versions(engine),
        "specs": [],
        "failures": [],
    }
    guard = project.guard(workspace, exclude=(emit,) if emit is not None else ())
    allow = (project.root,)
    checked = [_Checked(spec, {"spec": spec.name, "target": spec.target, "failures": []}) for spec in specs]
    record["specs"] = [c.record for c in checked]
    try:
        root = project.overlay(workspace, replace=replace)
        infos = forge.build(root, workspace.fresh("ast-out"), workspace.fresh("ast-cache"), project.profile, allow)
    except forge.BuildError as err:
        record["failures"].append(f"build: {err}")
        return _finish(record, checked, guard)

    for c in checked:
        _extract(c, infos, operators=len(specs) == 1)
    models = [c for c in checked if c.model is not None]
    stems: dict[str, str] = {}
    for c in list(models):
        stem = names(c.model)[0].removesuffix("Model")
        if stem in stems:
            c.record["failures"].append(f"extract: {stem} names the generated contracts of {stems[stem]} too")
            models.remove(c)
            c.model = None
        stems.setdefault(stem, c.spec.name)
    shared = operators_test([c.model for c in models], SHARED_OPERATORS) if len(specs) > 1 else None
    files = {}
    for c in models:
        files.update(c.generated.files)
    if shared is not None:
        files.update(shared.files)
    if emit is not None:
        emit.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (emit / name).write_text(text)
    for c in models:
        if vectors:
            try:
                c.vectors = sample(c.model, c.spec.impossible)
                stem = names(c.model)[0].removesuffix("Model")
                files[f"{stem}Vectors.t.sol"] = vectors_source(c.model, c.vectors, c.spec.link.test_import)
            except VectorError as err:
                c.record["failures"].append(f"vectors: {err}")
    for name, text in files.items():
        (root / project.generated / name).write_text(text)
    record["generated"] = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}

    for c in models:
        _theorems(c, only)
    if vectors:
        _vectors(checked, root, workspace, project, allow)
    if link:
        checks = [check for c in models for check in c.generated.checks]
        if shared is not None:
            checks += shared.checks
        linked = engine.run(root, workspace, tuple(checks), project.profile, allow)
        results = {r.check: r for r in linked.results}
        record["link"] = {"engine": engine.name, "problems": list(linked.problems)}
        record["failures"] += [f"link: {p}" for p in linked.problems]
        for c in models:
            c.record["link"] = [_check_record(results.pop(check)) for check in c.generated.checks]
            c.record["failures"] += [f"link: {r['contract']}.{r['check']} {r['verdict']}"
                                     for r in c.record["link"] if r["verdict"] != "PASS"]  # fmt: skip
        record["link"]["shared"] = [_check_record(r) for r in results.values()]
        record["failures"] += [f"link: {r['contract']}.{r['check']} {r['verdict']}"
                               for r in record["link"]["shared"] if r["verdict"] != "PASS"]  # fmt: skip
    else:
        record["link"] = None
        for c in models:
            c.record["link"] = None
    return _finish(record, checked, guard)


def load(specs: Sequence[Spec], workspace: Workspace) -> dict[str, Terms]:
    """The terms of each spec's model, by spec name, from one build of the project: for checks that use the models
    without a run, such as a check of each deployed configuration. Raises the first extraction error."""
    project = specs[0].project
    guard = project.guard(workspace)
    root = project.overlay(workspace)
    out, cache = workspace.fresh("ast-out"), workspace.fresh("ast-cache")
    infos = forge.build(root, out, cache, project.profile, (project.root,))
    loaded = {spec.name: Terms(extract(infos, spec.target, spec.reads).model) for spec in specs}
    changes = guard.changes()
    if changes:
        raise RuntimeError(f"the project changed while z3sol read it: {'; '.join(changes)}")
    return loaded


def _extract(c: _Checked, infos: list[dict], operators: bool) -> None:
    try:
        extraction = extract(infos, c.spec.target, c.spec.reads)
        c.model = dataclasses.replace(extraction.model, stem=c.spec.stem)
        c.generated = generate(c.model, c.spec.link, operators=operators)
    except (ExtractionError, LinkError) as err:
        c.record["failures"].append(f"extract: {err}")
        c.model = None
        return
    info = next(i for i in infos if c.spec.target.rpartition(":")[0] in i["output"]["sources"])
    c.record["versions"] = _versions(info, c.spec.target, extraction.sources)
    c.record["sources"] = extraction.sources
    c.record["model"] = {
        "inputs": [{"name": v.name, "type": v.type.solidity} for v in c.model.inputs],
        "reads": {r.var.name: r.source for r in c.model.reads},
        "callees": [f.name for f in c.model.callees],
    }


def _theorems(c: _Checked, only: tuple[str, ...] | None) -> None:
    q = Terms(c.model)
    c.record["theorems"] = []
    chosen = c.spec.theorems
    if only is not None:
        unknown = set(only) - {t.id for t in c.spec.theorems}
        if unknown:
            c.record["failures"].append(f"theorems: no theorem {', '.join(sorted(unknown))}")
        chosen = [t for t in c.spec.theorems if t.id in only]
        c.record["only"] = list(only)
    for t in chosen:
        try:
            result = prove(t, q)
        except TheoremError as err:
            c.record["failures"].append(f"theorem {t.id}: {err}")
            continue
        c.record["theorems"].append(dataclasses.asdict(result))
        if not result.ok:
            witness = f", witness {result.witness}" if result.witness not in (None, "WITNESS") else ""
            expected = "PROVED" if result.expected == "proved" else "COUNTEREXAMPLE"
            c.record["failures"].append(f"theorem {t.id}: {result.verdict}{witness}, expected {expected}")


def _vectors(checked: list[_Checked], root, workspace: Workspace, project: Project, allow) -> None:
    sampled = [c for c in checked if c.vectors is not None]
    for c in checked:
        c.record["vectors"] = None
    if not sampled:
        return
    contracts = tuple(names(c.model)[0].removesuffix("Model") + "Vectors" for c in sampled)
    statuses = forge.test(
        root, workspace.fresh("vectors-out"), workspace.fresh("vectors-cache"), contracts, project.profile, allow
    )
    for c, contract in zip(sampled, contracts, strict=True):
        mine = [status for (name, _), status in statuses.items() if name == contract]
        agree = sum(status == "Success" for status in mine)
        c.record["vectors"] = {"count": len(c.vectors), "agree": agree}
        if agree != len(c.vectors) or len(mine) != len(c.vectors):
            c.record["failures"].append(f"vectors: {agree} of {len(c.vectors)} agree with the emitted Solidity")


def _check_record(r) -> dict:
    return {"contract": r.check.contract, "check": r.check.function, "verdict": r.verdict, "time": r.time,
            "paths": r.paths}  # fmt: skip


def _finish(record: dict, checked: list[_Checked], guard) -> dict:
    for c in checked:
        c.record["ok"] = not c.record["failures"]
        record["failures"] += [f"{c.spec.name}: {failure}" for failure in c.record["failures"]]
    record["failures"] += [f"project: {change}" for change in guard.changes()]
    record["ok"] = not record["failures"]
    return record


def _project(root: pathlib.Path) -> dict:
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    return {"root": str(root), "commit": commit, "dirty": bool(git("status", "--porcelain")) if commit else None}


def _tool_versions(engine) -> dict:
    return {
        "python": platform.python_version(),
        "z3sol": importlib.metadata.version("z3sol"),
        "z3sol commit": _own_commit(),
        "z3 (theorems)": z3.get_full_version(),
        **solvers.versions(),
        **engine.versions(),
    }


def _own_commit() -> str | None:
    """The commit of this z3sol: from pip's record of a git install, or from the checkout of an editable one."""
    distribution = importlib.metadata.distribution("z3sol")
    direct = distribution.read_text("direct_url.json")
    if direct:
        data = json.loads(direct)
        if "vcs_info" in data:
            return data["vcs_info"].get("commit_id")
    here = pathlib.Path(__file__).resolve().parent
    result = subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True)
    if result.returncode:
        return None
    dirty = subprocess.run(["git", "-C", str(here), "status", "--porcelain"], capture_output=True, text=True)
    return result.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def _versions(info: dict, target: str, sources: dict[str, str]) -> dict:
    path, _, qualified = target.rpartition(":")
    contract = qualified.partition(".")[0]
    metadata = json.loads(info["output"]["contracts"][path][contract]["metadata"])
    versions = {"solc": metadata["compiler"]["version"]}
    for source in sources:
        if source.endswith(MATH_PATH):
            content = info["input"]["sources"][source]["content"]
            header = next((line for line in content.splitlines() if "OpenZeppelin Contracts" in line), "")
            blob = hashlib.sha1(f"blob {len(content.encode())}\0".encode() + content.encode()).hexdigest()
            versions["OpenZeppelin"] = {
                "header": header.lstrip("/ ").strip(),
                "path": source,
                "sha256": sources[source],
                "git blob": blob,
            }
    return versions


def summary(record: dict) -> str:
    """The record as lines for a terminal."""
    lines = []
    for spec in record["specs"]:
        lines.append(f"== {spec['spec']}: {spec['target']}")
        if "model" in spec:
            model = spec["model"]
            reads = ", ".join(f"{name} = {source}" for name, source in model["reads"].items())
            lines.append(f"model: {len(model['inputs'])} inputs; reads {reads}")
        for t in spec.get("theorems", []):
            mark = "" if t["ok"] else "   <-- unexpected"
            witness = f" {t['witness']}" if t["witness"] else ""
            shown = " ".join(f"{k}={v}" for k, v in t["counterexample"].items())
            solver = f" ({t['solver']})" if t["solver"] not in ("", "z3") else ""
            line = f"{t['verdict']}{witness} [{t['id']}] {t['statement']} [{t['time']:.2f}s]{solver} {shown}{mark}"
            lines.append(line.rstrip())
        if spec.get("vectors"):
            lines.append(f"vectors: {spec['vectors']['agree']} of {spec['vectors']['count']} agree")
        elif "vectors" in spec:
            lines.append("vectors: not run")
        for r in spec.get("link") or []:
            lines.append(_check_line(r))
        if spec.get("link") is None and "link" in spec:
            lines.append("link: not run")
    if record.get("link"):
        if record["link"]["shared"]:
            lines.append("== the operator-table rows and lemmas")
        lines += [_check_line(r) for r in record["link"]["shared"]]
    lines += [f"FAILED {failure}" for failure in record["failures"]]
    lines.append("ok" if record["ok"] else f"{len(record['failures'])} failure(s)")
    return "\n".join(lines)


def _check_line(r: dict) -> str:
    time_ = f" [{r['time']:.2f}s]" if r["time"] is not None else ""
    return f"{r['verdict']} {r['contract']}.{r['check']}{time_}"
