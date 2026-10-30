"""A run: extract each spec's model, prove its theorems, check its vectors, link it to the bytecode, and record each
result with the versions it rests on.

The specs of one run share a project. The run builds it once, runs the vectors of every model in one forge test, and
links every model in one Halmos run, with one test of the operator-table rows and lemmas that the models use. The three
share one build directory, so solc compiles each project file once. The record holds the time of each stage. Each stage
runs even when an earlier one fails, except that a spec without a model has nothing to check, so one run shows every
failure. An error in a stage, such as an exception in a theorem's code or a missing forge, is a failure of that stage,
with its traceback in the record when it is not one of solift's own errors. The run fails when a result is not the
expected one, and when the project changed during the run.

The record is JSON. `schema` numbers its format. Next to the results, it holds what they rest on: the versions of the
tools, the sha256 of each solc standard-JSON input of the build, of each theorem's query, of the spec's Python files
and of each file that the overlay adds, the profile, and the options of the link engine.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import importlib.metadata
import json
import os
import pathlib
import platform
import subprocess
import sys
import time
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field

import z3

from solift import forge, solvers
from solift.extract import ExtractionError, extract
from solift.halmos import HalmosEngine
from solift.link import Harness, Link, LinkError, generate, operators_test, placed
from solift.solidity import names
from solift.theorem import Terms, TheoremError, prove, theorem
from solift.vectors import VectorError, sample, vectors_source
from solift.workspace import Project, Workspace, WorkspaceError

MATH_PATH = "utils/math/Math.sol"
SCHEMA = 3
SHARED_OPERATORS = "Solift"
# The errors that say what went wrong without a traceback.
KNOWN_ERRORS = (
    forge.BuildError,
    ExtractionError,
    LinkError,
    TheoremError,
    VectorError,
    WorkspaceError,
    FileNotFoundError,
    subprocess.CalledProcessError,
)


@dataclass
class Spec:
    """What a run checks: the target function, its read map, its link, and its theorems. `impossible` names the
    outcomes (pays, zero, reverts) that the spec asserts no input reaches: a vector that reaches one fails the run. An
    outcome that z3 proves no input reaches needs no entry; the record lists it as unreachable. `stem` names the
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
    samples: object = None


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
        "project": _project(project),
        "replaced": sorted(replace or {}),
        "versions": {},
        "engine": {"name": engine.name, "options": engine.options()},
        "spec files": _spec_files(specs),
        "specs": [],
        "failures": [],
    }
    times = record["times"] = {}
    clock = _Clock(times)
    guard = project.guard(workspace, exclude=(emit,) if emit is not None else ())
    clock.stage("guard")
    allow = (project.root,)
    checked = [_Checked(spec, {"spec": spec.name, "target": spec.target, "failures": []}) for spec in specs]
    record["specs"] = [c.record for c in checked]
    try:
        record["versions"] = _tool_versions(engine)
        project, prepared = _prepared(specs)
        for c, spec in zip(checked, prepared, strict=True):
            c.spec = spec
        record["project"] = _project(project)
        build = (workspace.fresh("build-out"), workspace.fresh("build-cache"))
        root = project.overlay(workspace, replace=replace)
        infos = forge.build(root, *build, project.profile, allow)
    except Exception as err:
        record["failures"].append(_failure(record, "build", err))
        return _finish(record, checked, guard, clock)
    record["build"] = {"inputs": [_sha256(json.dumps(info["input"], sort_keys=True)) for info in infos]}
    clock.stage("build")

    for c in checked:
        _extract(c, infos, operators=len(specs) == 1, record=record)
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
                c.samples = sample(c.model, c.spec.impossible)
                stem = names(c.model)[0].removesuffix("Model")
                files[f"{stem}Vectors.t.sol"] = vectors_source(c.model, c.samples.vectors, c.spec.link.test_import)
            except Exception as err:
                c.record["failures"].append(_failure(record, "vectors", err))
    clock.stage("extract and sample the vectors")
    try:
        for name, text in files.items():
            project.write(root, name, text)
    except Exception as err:
        record["failures"].append(_failure(record, "generate", err))
        return _finish(record, checked, guard, clock)
    record["generated"] = {name: _sha256(text) for name, text in files.items()}

    for c in models:
        _theorems(c, only, record)
    clock.stage("theorems")
    if vectors:
        _vectors(checked, root, build, project, allow, record)
        clock.stage("vectors (forge test)")
    if link:
        _link(models, shared, root, workspace, build, project, allow, engine, record)
        clock.stage("link (Halmos)")
    else:
        record["link"] = None
        for c in models:
            c.record["link"] = None
    for c in models:
        status = _link_status(c, record)
        for t in c.record["theorems"]:
            t["link"] = status
    return _finish(record, checked, guard, clock)


def _prepared(specs: list[Spec]) -> tuple[Project, list[Spec]]:
    """The specs' project with its inferred fields resolved and each `Harness` added to its overlay, and the specs on
    it with each harness named as `path:Contract`. Specs may share a harness, and two harnesses at one place fail."""
    project = specs[0].project.resolve()
    add, setups = dict(project.add), []
    for spec in specs:
        setup = spec.link.setup
        if isinstance(setup, Harness):
            at, source = setup.destination(project.generated), pathlib.Path(setup.path).resolve()
            if at in add and pathlib.Path(add[at]).resolve() != source:
                raise WorkspaceError(f"two harnesses go to {at}: {add[at]} and {setup.path}")
            add[at] = source
            setup = f"{at}:{setup.contract}"
        setups.append(setup)
    project = dataclasses.replace(project, add=add)
    return project, [
        dataclasses.replace(spec, project=project, link=dataclasses.replace(spec.link, setup=setup))
        for spec, setup in zip(specs, setups, strict=True)
    ]


def _link(models, shared, root, workspace, build, project, allow, engine, record) -> None:
    """Runs the link checks of every model and of the shared operator rows in one Halmos run, and records each."""
    checks = [check for c in models for check in c.generated.checks]
    if shared is not None:
        checks += shared.checks
    here = dict(zip(checks, placed(checks, project.generated), strict=True))
    try:
        linked = engine.run(root, workspace, tuple(here.values()), project.profile, allow, build=build)
    except Exception as err:
        failure = _failure(record, "link", err)
        record["link"] = {"engine": engine.name, "problems": [failure], "shared": []}
        record["failures"].append(failure)
        for c in models:
            c.record["link"] = []
        return
    unplaced = {v: k for k, v in here.items()}
    results = {unplaced.get(r.check, r.check): r for r in linked.results}
    record["link"] = {"engine": engine.name, "problems": list(linked.problems)}
    record["failures"] += [f"link: {p}" for p in linked.problems]
    for c in models:
        c.record["link"] = [_check_record(results.pop(check)) for check in c.generated.checks]
        c.record["failures"] += [f"link: {r['contract']}.{r['check']} {r['verdict']}"
                                 for r in c.record["link"] if r["verdict"] != "PASS"]  # fmt: skip
    record["link"]["shared"] = [_check_record(r) for r in results.values()]
    record["failures"] += [f"link: {r['contract']}.{r['check']} {r['verdict']}"
                           for r in record["link"]["shared"] if r["verdict"] != "PASS"]  # fmt: skip


def _link_status(c: _Checked, record: dict) -> str:
    """passed when the model's link check and every shared check passed in a run without problems, not-run when the
    run did not link, and failed otherwise."""
    if record.get("link") is None:
        return "not-run"
    run = record["link"]
    passed = c.record["link"] and all(r["verdict"] == "PASS" for r in c.record["link"] + run["shared"])
    return "passed" if passed and not run["problems"] else "failed"


def _failure(record: dict, stage: str, err: Exception) -> str:
    """The failure of a stage from the error it raised, with the traceback in the record for an error that is not one
    of KNOWN_ERRORS. It is called where the error is handled."""
    if isinstance(err, KNOWN_ERRORS):
        return f"{stage}: {err}"
    record.setdefault("tracebacks", []).append({"stage": stage, "traceback": traceback.format_exc()})
    return f"{stage}: {type(err).__name__}: {err}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class _Clock:
    """Records the seconds since the last stage under each stage's name."""

    def __init__(self, times: dict):
        self.times = times
        self.last = time.perf_counter()

    def stage(self, name: str) -> None:
        now = time.perf_counter()
        self.times[name] = round(now - self.last, 2)
        self.last = now


def load(specs: Sequence[Spec], workspace: Workspace) -> dict[str, Terms]:
    """The terms of each spec's model, by spec name, from one build of the project: for checks that use the models
    without a run, such as a check of each deployed configuration. Raises the first extraction error."""
    project = specs[0].project.resolve()
    guard = project.guard(workspace)
    root = project.overlay(workspace)
    out, cache = workspace.fresh("ast-out"), workspace.fresh("ast-cache")
    infos = forge.build(root, out, cache, project.profile, (project.root,))
    loaded = {spec.name: Terms(extract(infos, spec.target, spec.reads).model) for spec in specs}
    changes = guard.changes()
    if changes:
        raise RuntimeError(f"the project changed while solift read it: {'; '.join(changes)}")
    return loaded


def _extract(c: _Checked, infos: list[dict], operators: bool, record: dict) -> None:
    try:
        extraction = extract(infos, c.spec.target, c.spec.reads)
        c.model = dataclasses.replace(extraction.model, stem=c.spec.stem)
        c.generated = generate(c.model, c.spec.link, operators=operators)
    except Exception as err:
        c.record["failures"].append(_failure(record, "extract", err))
        c.model = None
        return
    info = next(i for i in infos if c.spec.target.rpartition(":")[0] in i["output"]["sources"])
    c.record["versions"] = _versions(info, c.spec.target, extraction.sources)
    c.record["sources"] = extraction.sources
    c.record["model"] = {
        "inputs": [{"name": v.name, "type": v.type.solidity} for v in c.model.inputs],
        "reads": {r.var.name: r.source for r in c.model.reads},
        "callees": [f.name for f in c.model.callees],
        "effects": [{"kind": e.kind, "place": e.place, "source": e.source, "at": str(e.src)} for e in c.model.effects],
    }


def _theorems(c: _Checked, only: tuple[str, ...] | None, record: dict) -> None:
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
        except Exception as err:
            c.record["failures"].append(_failure(record, f"theorem {t.id}", err))
            continue
        c.record["theorems"].append(dataclasses.asdict(result))
        if not result.ok:
            witness = f", witness {result.witness}" if result.witness not in (None, "WITNESS") else ""
            expected = "PROVED" if result.expected == "proved" else "COUNTEREXAMPLE"
            c.record["failures"].append(f"theorem {t.id}: {result.verdict}{witness}, expected {expected}")


def _vectors(checked: list[_Checked], root, build, project: Project, allow, record: dict) -> None:
    sampled = [c for c in checked if c.samples is not None]
    for c in checked:
        c.record["vectors"] = None
    if not sampled:
        return
    contracts = tuple(names(c.model)[0].removesuffix("Model") + "Vectors" for c in sampled)
    try:
        statuses = forge.test(root, *build, contracts, project.profile, allow)
    except Exception as err:
        failure = _failure(record, "vectors", err)
        for c in sampled:
            c.record["failures"].append(failure)
        return
    for c, contract in zip(sampled, contracts, strict=True):
        count = len(c.samples.vectors)
        mine = {key: status for key, status in statuses.items() if key[1] == contract}
        failures = vector_failures(contract, f"{project.generated}/{contract}.t.sol", count, mine)
        agree = sum(status == "Success" for (_, _, name), status in mine.items() if name.startswith("test_vector_"))
        c.record["vectors"] = {
            "count": count,
            "agree": agree if not failures else 0,
            "outcomes": c.samples.outcomes,
            "boundaries": c.samples.boundaries,
        }
        c.record["failures"] += failures


def vector_failures(contract: str, path: str, count: int, statuses: dict) -> list[str]:
    """Why the forge results of a vectors contract do not show that every vector agrees: each result must come from
    the generated file, and the tests must be exactly test_vector_000 up to the count, each a success."""
    failures = []
    paths = sorted({p for p, c, _ in statuses if c == contract} - {path})
    if paths:
        failures.append(f"vectors: the results come from {', '.join(paths)}, not {path}")
    expected = {f"test_vector_{n:03d}" for n in range(count)}
    passed = {name for (p, c, name), status in statuses.items() if (p, c) == (path, contract) and status == "Success"}
    if passed != expected or len(statuses) != count:
        failures.append(f"vectors: {len(passed & expected)} of {count} agree with the emitted Solidity")
    return failures


def _check_record(r) -> dict:
    return {"contract": r.check.contract, "check": r.check.function, "verdict": r.verdict, "time": r.time,
            "paths": r.paths}  # fmt: skip


def _finish(record: dict, checked: list[_Checked], guard, clock: _Clock) -> dict:
    for c in checked:
        c.record["ok"] = not c.record["failures"]
        record["failures"] += [f"{c.spec.name}: {failure}" for failure in c.record["failures"]]
    try:
        record["failures"] += [f"project: {change}" for change in guard.changes()]
    except Exception as err:
        record["failures"].append(_failure(record, "project", err))
    clock.stage("check the project")
    record["ok"] = not record["failures"]
    return record


def _project(project: Project) -> dict:
    """The project's root, commit and profile, and the sha256 of each file that the overlay adds to it."""
    root = project.root

    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    added = {rel: hashlib.sha256(pathlib.Path(source).read_bytes()).hexdigest() for rel, source in project.add.items()}
    return {
        "root": str(root),
        "commit": commit,
        "dirty": bool(git("status", "--porcelain")) if commit else None,
        "profile": project.profile,
        "link": list(project.link or ()),
        "copy": list(project.copy or ()),
        "generated": project.generated,
        "added": added,
    }


def _spec_files(specs) -> dict[str, str]:
    """The sha256 of each loaded Python file of the packages that define the specs' theorems, by its path from the
    package's parent directory: the spec code that the results rest on."""
    tops = set()
    for spec in specs:
        for t in spec.theorems:
            for f in (t.goal, t.given, t.witness):
                if getattr(f, "__module__", None):
                    tops.add(f.__module__.partition(".")[0])
    files = {}
    for top in sorted(tops):
        module = sys.modules.get(top)
        if module is None or not getattr(module, "__file__", None):
            continue
        roots = [pathlib.Path(p) for p in getattr(module, "__path__", [])] or [pathlib.Path(module.__file__)]
        base = roots[0].parent
        for loaded in list(sys.modules.values()):
            path = pathlib.Path(getattr(loaded, "__file__", None) or "/")
            if path.suffix == ".py" and any(path == r or r in path.parents for r in roots):
                files[os.path.relpath(path, base)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(files.items()))


def _tool_versions(engine) -> dict:
    return {
        "python": platform.python_version(),
        "solift": importlib.metadata.version("solift"),
        "solift commit": _own_commit(),
        "z3 (theorems)": z3.get_full_version(),
        **solvers.versions(),
        **engine.versions(),
    }


def _own_commit() -> str | None:
    """The commit of this solift: from pip's record of a git install, or from the checkout of an editable one."""
    distribution = importlib.metadata.distribution("solift")
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
            mark = "" if t["ok"] else "   <-- unexpected" + (f": {t['detail']}" if t["detail"] else "")
            witness = f" {t['witness']}" if t["witness"] else ""
            shown = " ".join(f"{k}={v}" for k, v in t["counterexample"].items())
            solver = f" ({t['solver']})" if t["solver"] not in ("", "z3") else ""
            line = f"{t['verdict']}{witness} [{t['id']}] {t['statement']} [{t['time']:.2f}s]{solver} {shown}{mark}"
            lines.append(line.rstrip())
            lines += [f"    {note}" for note in _scope(t, spec["target"])]
        if spec.get("vectors"):
            vectors = spec["vectors"]
            unreachable = [o for o, status in vectors.get("outcomes", {}).items() if status == "unreachable"]
            note = f" ({', '.join(unreachable)}: no input reaches it)" if unreachable else ""
            lines.append(f"vectors: {vectors['agree']} of {vectors['count']} agree{note}")
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


def _scope(t: dict, target: str) -> list[str]:
    """What a theorem's result does not cover, as notes under its line."""
    notes = []
    if t.get("lemma_domain") in ("not-established", "unknown"):
        notes.append("rests on Math.mulDiv where an operand reaches 2**128, which this build does not prove")
    if t.get("callees"):
        them = "it" if len(t["callees"]) == 1 else "them"
        where = f", which the link covers where {target} calls {them}" if t.get("link") == "passed" else ""
        notes.append(f"a theorem of the integer model of {', '.join(t['callees'])}{where}")
    return notes


def _check_line(r: dict) -> str:
    time_ = f" [{r['time']:.2f}s]" if r["time"] is not None else ""
    return f"{r['verdict']} {r['contract']}.{r['check']}{time_}"
