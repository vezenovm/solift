"""Mutants. Each changes one expression of one function, and a theorem must reject it: the run of the mutant fails on
that theorem, and the theorems in `holds` still prove. The model follows the contract, so the link of a mutant still
passes. The mutants marked `link` show that with a Halmos run."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Mutant:
    """`spec` names the spec whose run must reject the mutant, `function` the function of `path` that it changes,
    `breaks` the theorem that must fail, and `holds` the theorems that must still prove."""

    id: str
    old: str
    new: str
    breaks: str
    spec: str
    path: str
    function: str
    holds: tuple[str, ...] = ()
    link: bool = False


def apply(source: str, mutant: Mutant) -> str:
    """The source with the mutant applied. The changed text must occur exactly once in the file, inside a function of
    the mutant's function name: one of several of that name, as for an overloaded function."""
    bodies = []
    start = source.find(f"function {mutant.function}(")
    while start != -1:
        indent = source[source.rfind("\n", 0, start) + 1 : start]
        end = source.find(f"\n{indent}}}", start)
        bodies.append(source[start : end if end != -1 else len(source)])
        start = source.find(f"function {mutant.function}(", start + 1)
    if source.count(mutant.old) != 1 or sum(body.count(mutant.old) for body in bodies) != 1:
        raise ValueError(f"{mutant.id}: {mutant.old!r} does not occur exactly once, inside {mutant.function}")
    return source.replace(mutant.old, mutant.new)


def problems(mutant: Mutant, record: dict) -> list[str]:
    """Why the run of a mutant does not show that its theorem rejects it. The run must fail on `breaks` with a complete
    counterexample and a witness, and on nothing else: each theorem in `holds` proves with a witness, and every other
    check passes."""
    (spec,) = record["specs"]
    theorems = {t["id"]: t for t in spec.get("theorems", [])}
    expected = f"{spec['spec']}: theorem {mutant.breaks}: COUNTEREXAMPLE, expected PROVED"
    found = [f for f in record["failures"] if f != expected]
    broken = theorems.get(mutant.breaks, {})
    if broken.get("verdict") != "COUNTEREXAMPLE":
        found.append(f"{mutant.breaks} is {broken.get('verdict')}, not COUNTEREXAMPLE")
    elif broken.get("detail"):
        found.append(f"{mutant.breaks}: {broken['detail']}")
    for held in mutant.holds:
        if not theorems.get(held, {}).get("ok"):
            found.append(f"{held} is not ok")
    if mutant.link and spec.get("link") is None:
        found.append("link: not run")
    return found
