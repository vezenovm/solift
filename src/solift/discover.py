"""Discovery: the read map that a target needs, found by the extractor itself.

`discover` extracts the target with an empty read map. Each time the extractor stops at a read that the read map does
not name (a storage variable or field, a mapping entry, a field of a struct parameter, a field of `tx`, `block` or
`msg`, or an external call), it adds the read under a proposed input name and extracts again. It ends with the model,
or at the first error that is not a missing read, such as a loop, which it reports with `complete` false. So the read
map it gives is one that the extractor takes, and nothing in it is a guess about the extractor's rules.

It never cuts an internal call: a cut changes what the link proves, so it lists the internal calls of the model as
candidates for the user to choose from. It lists the writes and events that the model leaves out, which the link does
not compare.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from solift.extract import ExtractionError, Missing, declared, extract
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
    """The reads that the target makes, in the order the extractor met them, each with its proposed input name; the
    model with them when `complete`; otherwise the error that stopped the discovery."""

    target: str
    reads: list[tuple[Missing, str]] = field(default_factory=list)
    complete: bool = False
    diagnostic: str = ""
    model: Model | None = None
    given: dict[str, str] = field(default_factory=dict)

    @property
    def read_map(self) -> dict[str, str]:
        """The given reads, then the reads that the discovery found."""
        return {**self.given, **{missing.key: name for missing, name in self.reads}}

    @property
    def cuts(self) -> list[str]:
        return [f.name for f in self.model.callees] if self.model is not None else []

    @property
    def effects(self) -> tuple[Effect, ...]:
        return self.model.effects if self.model is not None else ()


def discover(build_infos: list[dict], target: str, reads: dict[str, str] | None = None) -> Discovery:
    """The reads of the target, starting from `reads`."""
    found = Discovery(target, given=dict(reads or {}))
    taken = declared(build_infos, target) | RESERVED | set((reads or {}).values())
    read_map = dict(reads or {})
    for _ in range(LIMIT):
        try:
            found.model = extract(build_infos, target, read_map).model
        except ExtractionError as err:
            if err.missing is None or err.missing.key in read_map:
                found.diagnostic = str(err)
                return found
            name = _propose(err.missing.key, taken)
            taken.add(name)
            read_map[err.missing.key] = name
            found.reads.append((err.missing, name))
            continue
        found.complete = True
        return found
    found.diagnostic = f"the target makes more than {LIMIT} reads"
    return found


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
    for key, name in found.read_map.items():
        if key in OBSERVED:
            observed.append(f"    {name!r}: {OBSERVED[key]!r},")
        elif key in CHEATCODES:
            adapters.append(f"    {name!r}: {CHEATCODES[key].format(name=name)!r},")
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
    lines.append("),")
    return "\n".join(lines)


def report(found: Discovery) -> str:
    """The discovery as lines for a terminal: the reads, the cut candidates, the effects and the spec skeleton."""
    function = found.target.rpartition(":")[2].partition(".")[2].partition("(")[0]
    state = "complete" if found.complete else f"incomplete: {found.diagnostic}"
    lines = [f"== {found.target}: {len(found.read_map)} reads, {state}", "", skeleton(found, function)]
    if found.cuts:
        lines += ["", "internal calls that a read map entry can cut (by the call's source text):"]
        lines += [f"  {name}" for name in found.cuts]
    if found.effects:
        lines += ["", "writes and events that the model leaves out, which the link does not compare:"]
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
    }
