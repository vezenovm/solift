"""Discovery: the read map that a target needs, found by the extractor itself.

`discover` extracts the target with an empty read map. Each time the extractor stops at a read that the read map does
not name (a storage variable or field, a mapping entry, a field of a struct parameter, a field of `tx`, `block` or
`msg`, or an external call), it adds the read under a proposed input name and extracts again. It ends with the model,
or at the first error that is not a missing read, such as a loop, which it reports with `complete` false. So the read
map it gives is one that the extractor takes, and nothing in it is a guess about the extractor's rules.

It adds outputs the same way: a write whose value can revert, or an argument of an external call that can, is an output
under a proposed name, and a function without a value gets each of its writes and external call arguments as an output.
A write that some paths make gets its value before the call in the read map.

It never cuts an internal call: a cut changes what the link proves, so it lists the internal calls of the model as
candidates for the user to choose from. It lists the writes, external calls and events that the model leaves out, which
the link does not compare, as candidates for the output map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from solift.extract import ExtractionError, Missing, Wanted, declared, extract
from solift.ir import BUILTINS, Effect, Model, reserved
from solift.link import RESERVED

LIMIT = 200
# The cheatcode that sets each environment field, for the adapters. tx.gasprice has none: Halmos makes it symbolic, so
# the link observes it.
CHEATCODES = {
    "block.timestamp": "vm.warp({name});",
    "block.number": "vm.roll({name});",
    "block.basefee": "vm.fee({name});",
    "block.chainid": "vm.chainId({name});",
    "block.prevrandao": "vm.prevrandao(bytes32({name}));",
    "block.coinbase": "vm.coinbase({name});",
}
OBSERVED = {"tx.gasprice": "tx.gasprice"}


@dataclass
class Discovery:
    """The reads that the target makes, in the order the extractor met them, each with its proposed input name, and the
    outputs that it needs, each with its proposed name or names; the model with them when `complete`; otherwise the
    error that stopped the discovery."""

    target: str
    reads: list[tuple[Missing, str]] = field(default_factory=list)
    complete: bool = False
    diagnostic: str = ""
    model: Model | None = None
    given: dict[str, str] = field(default_factory=dict)
    found_effects: tuple[Effect, ...] = ()
    outputs: dict = field(default_factory=dict)
    taken: set[str] = field(default_factory=set)

    @property
    def read_map(self) -> dict[str, str]:
        """The given reads, then the reads that the discovery found."""
        return {**self.given, **{missing.key: name for missing, name in self.reads}}

    @property
    def cuts(self) -> list[str]:
        return [f.name for f in self.model.callees] if self.model is not None else []

    @property
    def effects(self) -> tuple[Effect, ...]:
        return self.model.effects if self.model is not None else self.found_effects


def discover(build_infos: list[dict], target: str, reads: dict[str, str] | None = None) -> Discovery:
    """The reads of the target, starting from `reads`."""
    found = Discovery(target, given=dict(reads or {}))
    try:
        taken = found.taken = declared(build_infos, target) | RESERVED | set((reads or {}).values())
    except ExtractionError as err:
        found.diagnostic = str(err)
        return found
    read_map = dict(reads or {})
    for _ in range(LIMIT):
        try:
            found.model = extract(build_infos, target, read_map, found.outputs).model
        except ExtractionError as err:
            if err.missing is not None and err.missing.key not in read_map:
                output = found.outputs.get(err.missing.key)
                before = f"{output}Before" if isinstance(output, str) and f"{output}Before" not in taken else None
                name = before or _propose(err.missing.key, taken)
                taken.add(name)
                read_map[err.missing.key] = name
                found.reads.append((err.missing, name))
                continue
            wanted = [err.output] if err.output is not None else []
            if err.effects and not found.outputs:
                for e in err.effects:
                    if e.kind == "write":
                        wanted.append(Wanted(e.place))
                    elif e.kind == "call":
                        wanted += [Wanted(e.place, n, e.arguments) for n, a in enumerate(e.arguments) if _value(a)]
            if wanted and _add_outputs(found, wanted):
                continue
            found.diagnostic = str(err)
            found.found_effects = err.effects
            return found
        found.complete = True
        return found
    found.diagnostic = f"the target makes more than {LIMIT} reads"
    return found


def _value(argument: str) -> bool:
    """Whether a call argument is a value that a function computes, and not a field of `tx`, `block` or `msg`, a
    literal, a constant by its upper case name, or `address(this)`."""
    fixed = r"(tx|block|msg)\.\w+|address\(this\)|[0-9][\w.]*|true|false|[A-Z][A-Z0-9_]*"
    return re.fullmatch(fixed, argument) is None


def _add_outputs(found: Discovery, wanted: list[Wanted]) -> bool:
    """Adds each wanted output to the discovery's output map under a proposed name: a name for a write, and one for an
    argument of a call, among None for the others. Whether it added one."""
    added = False
    for want in wanted:
        if want.index is None:
            if want.key in found.outputs:
                continue
            found.outputs[want.key] = _propose(want.key, found.taken)
            found.taken.add(found.outputs[want.key])
        else:
            names = list(found.outputs.get(want.key) or (None,) * len(want.arguments))
            if names[want.index] is not None:
                continue
            names[want.index] = _propose(want.arguments[want.index], found.taken)
            found.taken.add(names[want.index])
            found.outputs[want.key] = tuple(names)
        added = True
    return added


def _propose(key: str, taken: set[str]) -> str:
    """An input name for a read: the field of a struct (`cap` for `$config.cap`), the mapping of an entry (`balances`
    for `$balances[_who]`), the function of an external call (`balanceOf` for `TOKEN.balanceOf(address(this))`), or the
    camel case of an environment field (`blockTimestamp`). A name that the target already uses takes the name before
    it as a prefix (`infoRate` for `$info.rate`), then a number."""
    if re.fullmatch(r"(tx|block|msg)\.\w+", key):
        candidates = [_camel(key.split("."))]
    else:
        call = re.search(r"(\w+)\((?:[^()]|\([^()]*\))*\)$", key)
        text = call.group(1) if call is not None else key
        while re.search(r"\[[^\[\]]*\]", text):
            text = re.sub(r"\[[^\[\]]*\]", "", text)
        names = [n.lstrip("$_") for n in re.findall(r"[A-Za-z_$][\w$]*", text)]
        names = [n.lower() if n.isupper() else n for n in names if n] or ["value"]
        candidates = [names[-1], *([_camel(names[-2:])] if len(names) > 1 else [])]
    candidates = [_identifier(c) for c in candidates]
    for name in candidates:
        if name not in taken:
            return name
    n = 2
    while f"{candidates[-1]}{n}" in taken:
        n += 1
    return f"{candidates[-1]}{n}"


def _identifier(name: str) -> str:
    """The name as an input name: only letters, digits and underscores, not starting with a digit, and no word that
    Solidity reserves or a global that it would shadow."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", name) or "value"
    if name[0].isdigit():
        name = f"v{name}"
    return f"{name}_" if reserved(name) or name in BUILTINS else name


def _camel(names: list[str]) -> str:
    return names[0] + "".join(n[0].upper() + n[1:] for n in names[1:])


def skeleton(found: Discovery, function: str) -> str:
    """The read map and a link for the spec, with placeholders where only the user knows the harness."""
    lines = ["reads={"]
    lines += [f"    {key!r}: {name!r},  # given" for key, name in found.given.items()]
    for missing, name in found.reads:
        lines.append(f"    {missing.key!r}: {name!r},  # {missing.what}, {missing.src}")
    lines.append("},")
    adapters, observed = [], []
    balances = {m.key for m, _ in found.reads if m.what == "the balance of an address"}
    for key, name in found.read_map.items():
        if key in OBSERVED:
            observed.append(f"    {name!r}: {OBSERVED[key]!r},")
        elif key in CHEATCODES:
            adapters.append(f"    {name!r}: {CHEATCODES[key].format(name=name)!r},")
        elif key in balances:
            holder = key.removesuffix(".balance")
            holder = "address(<the contract in the harness>)" if holder == "address(this)" else holder
            adapters.append(f"    {name!r}: 'vm.deal({holder}, {name});',")
        else:
            adapters.append(f"    {name!r}: '/* set {name}, which the target reads as {key} */',")
    params = ", ".join(v.name for v in found.model.function.params) if found.model is not None else "..."
    lines += [
        "link=Link(",
        '    setup="<path of the harness>:<its contract>",',
        f'    call="<the contract in the harness>.<an external function that runs {function}>({params})",',
        "    adapters={",
        *(f"    {line}" for line in adapters),
        "    },",
    ]
    if observed:
        lines += ["    observed={", *(f"    {line}" for line in observed), "    },"]
    names = [n for value in found.outputs.values() for n in (value if isinstance(value, tuple) else (value,)) if n]
    if names:
        lines += ["    post={", *(f"        {n!r}: '/* read {n} after the call */'," for n in names), "    },"]
    lines.append("),")
    candidates = [e for e in found.effects if e.kind in ("write", "call") and e.place not in found.outputs]
    if found.outputs or candidates:
        entries = [f"    {key!r}: {value!r},  # an output" for key, value in found.outputs.items()]
        entries += [f"    # {e.place!r}: ...,  # a {e.kind}, {e.src}: an output if named" for e in candidates]
        lines = ["outputs={", *entries, "},", *lines]
    return "\n".join(lines)


def report(found: Discovery) -> str:
    """The discovery as lines for a terminal: the reads, the cut candidates, the effects and the spec skeleton, which a
    discovery that found nothing leaves out."""
    function = found.target.rpartition(":")[2].partition(".")[2].partition("(")[0]
    state = "complete" if found.complete else f"incomplete: {found.diagnostic}"
    lines = [f"== {found.target}: {len(found.read_map)} reads, {state}"]
    if found.model is not None or found.read_map:
        lines += ["", skeleton(found, function)]
    if found.cuts:
        lines += ["", "internal calls that a read map entry can cut (by the call's source text):"]
        lines += [f"  {name}" for name in found.cuts]
    if found.effects:
        lines += ["", "writes, calls and events that the model leaves out, which the link does not compare:"]
        lines += [f"  {e.kind} {e.place}  ({e})" for e in found.effects]
    return "\n".join(lines)


def as_json(found: Discovery) -> dict:
    types = {r.source: r.var.type.solidity for r in found.model.reads} if found.model is not None else {}
    return {
        "target": found.target,
        "complete": found.complete,
        "diagnostic": found.diagnostic,
        "reads": [
            *(
                {"key": key, "name": name, "what": "given", "at": None, "type": types.get(key)}
                for key, name in found.given.items()
            ),
            *(
                {"key": m.key, "name": name, "what": m.what, "at": str(m.src), "type": types.get(m.key)}
                for m, name in found.reads
            ),
        ],
        "cuts": found.cuts,
        "effects": [{"kind": e.kind, "place": e.place, "source": e.source, "at": str(e.src)} for e in found.effects],
        "outputs": [{"key": key, "names": value} for key, value in found.outputs.items()],
    }
