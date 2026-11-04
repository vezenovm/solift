"""The IR: one loop-free Solidity function as typed expressions and statements.

A function returns one value or several: a function of several returns a tuple, whose type has the type of each part.

A model is a function whose reads from outside it (storage, external view calls, `tx` and `block` fields, and the fields
of a struct parameter) are inputs, like its parameters. It has the function's value and its reverts, and its outputs:
the storage writes and the arguments of external calls that the spec names. The other writes, calls and events are
left out (`Model.effects` lists those). The modeled function returns its outputs after its own values. smt.py turns a
model into z3 integer terms, and solidity.py into pure Solidity. ops.py holds the operator table that ties the two
together and checks the types of each operation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The keywords and reserved words of Solidity, which are no identifier, and the elementary type names. A contextual word
# such as `error` or `global` is an identifier.
KEYWORDS = frozenset(
    """abstract after alias anonymous apply as assembly auto break byte calldata case catch constant constructor
    continue contract copyof days default define delete do else emit enum ether event external fallback false final
    for function gwei hex hours if immutable implements import in indexed inline interface internal is let library
    macro mapping match memory minutes modifier mutable new null of override partial payable pragma private promise
    public pure receive reference relocatable return returns sealed seconds sizeof static storage struct supports
    switch throw true try type typedef typeof unchecked unicode using var view virtual weeks wei while years address
    bool string""".split()
)
ELEMENTARY = re.compile(r"(u?int|bytes|u?fixed)(\d+(x\d+)?)?")
# Global names that a declaration can shadow, with a warning: an input name avoids them, and the extractor allows them.
BUILTINS = frozenset(
    """abi block msg tx now this super gasleft blockhash blobhash keccak256 sha256 ripemd160 ecrecover addmod mulmod
    selfdestruct require assert revert""".split()
)


def reserved(name: str) -> bool:
    """Whether Solidity reserves the name, so that the emitted Solidity cannot use it as an identifier."""
    return name in KEYWORDS or ELEMENTARY.fullmatch(name) is not None


@dataclass(frozen=True)
class Src:
    """A place in a source file, for error messages."""

    file: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}"


@dataclass(frozen=True)
class Type:
    """`uint<bits>`, `bool`, `address`, `enum`, `tuple`, or `literal`: a number literal, which takes the type of the
    operand next to it. An enum has its Solidity name, such as `Kinds.Kind`, and its number of members. A contract or
    interface value is its address. A tuple is the values of a function of several returns, with the type of each in
    `parts`."""

    kind: str
    bits: int = 0
    name: str = ""
    members: int = 0
    parts: tuple[Type, ...] = ()

    @property
    def solidity(self) -> str:
        """The type as Solidity writes it, and a tuple as the list of its parts' types: `uint256, bool`."""
        if self.kind == "uint":
            return f"uint{self.bits}"
        if self.kind in ("bool", "address"):
            return self.kind
        if self.kind == "enum":
            return self.name
        if self.kind == "tuple":
            return ", ".join(part.solidity for part in self.parts)
        raise ValueError(f"a {self.kind} has no Solidity type of its own")

    @property
    def bound(self) -> int:
        """Values of an unsigned type, an address or an enum lie in [0, bound)."""
        if self.kind == "uint":
            return 2**self.bits
        if self.kind == "address":
            return 2**160
        if self.kind == "enum":
            return self.members
        raise ValueError(f"{self.kind} has no bound")

    @property
    def is_number(self) -> bool:
        return self.kind in ("uint", "literal")


def uint(bits: int) -> Type:
    if bits % 8 or not 8 <= bits <= 256:
        raise ValueError(f"no type uint{bits}")
    return Type("uint", bits)


def enum(name: str, members: int) -> Type:
    return Type("enum", 8, name, members)


def tuple_of(parts: tuple[Type, ...]) -> Type:
    """The type of a function's returns: the one type, or the tuple of several."""
    if not parts:
        raise ValueError("a function returns at least one value")
    return parts[0] if len(parts) == 1 else Type("tuple", parts=tuple(parts))


def parts(type_: Type) -> tuple[Type, ...]:
    """The types of the values of a function's returns: one, or each part of a tuple."""
    return type_.parts if type_.kind == "tuple" else (type_,)


UINT256 = uint(256)
BOOL = Type("bool")
ADDRESS = Type("address", 160)
LITERAL = Type("literal")


class Expr:
    type: Type
    src: Src | None


@dataclass(frozen=True, eq=False)
class Var(Expr):
    """A parameter, a local, or a read. `input` marks an input of the model: a parameter of the modeled function or a
    read. A parameter of a called function is not an input. A parameter of the modeled function that its body assigns
    is an input, and a local of the same name, which the body declares from it first and then reads and assigns."""

    name: str
    type: Type = UINT256
    input: bool = False
    src: Src | None = None


@dataclass(frozen=True, eq=False)
class Const(Expr):
    """A literal (`text` as written, such as `1e18`), an enum member (`text` such as `Kinds.Kind.Small`, and its
    index as the value), or a named constant (`name`, such as `WAD`, with its declared type)."""

    value: int | bool
    type: Type = LITERAL
    text: str | None = None
    name: str | None = None
    src: Src | None = None


@dataclass(frozen=True, eq=False)
class Op(Expr):
    """An operator of the table in ops.py. Build it with `ops.make`, which checks the operand types."""

    op: str
    args: tuple[Expr, ...]
    type: Type
    src: Src | None = None


@dataclass(frozen=True, eq=False)
class Call(Expr):
    """A call to an internal function of the model, by name. A call to a function of several returns is a tuple: it is
    the whole value of a return or an unpack."""

    function: str
    args: tuple[Expr, ...]
    type: Type
    src: Src | None = None


@dataclass(frozen=True, eq=False)
class Tuple(Expr):
    """Several values, as `(a, b)` in a return or on the right of an unpack."""

    args: tuple[Expr, ...]
    type: Type
    src: Src | None = None


@dataclass(frozen=True)
class Let:
    """A local declaration with its initial value."""

    var: Var
    expr: Expr
    src: Src | None = None


@dataclass(frozen=True)
class Assign:
    """An assignment to a local. A compound assignment such as `x += e` is `x = x + e`."""

    var: Var
    expr: Expr
    src: Src | None = None


@dataclass(frozen=True)
class Unpack:
    """The parts of a tuple into locals, as `(, uint256 y) = f(a);` (`declare`, a declaration) or `(x, y) = (y, x);`
    (an assignment to declared locals). `vars` has a local or None for each part. The tuple is evaluated whole before
    any local changes."""

    vars: tuple[Var | None, ...]
    expr: Expr
    declare: bool
    src: Src | None = None


@dataclass(frozen=True)
class Return:
    """The value of the function: one expression, or a tuple for a function of several returns."""

    expr: Expr
    src: Src | None = None


@dataclass(frozen=True)
class Revert:
    """A revert, as of `revert(...)`, `revert E(...)`, or a `require` or `assert` whose condition is false."""

    src: Src | None = None


@dataclass(frozen=True)
class If:
    cond: Expr
    then: tuple[Stmt, ...]
    orelse: tuple[Stmt, ...] = ()
    src: Src | None = None


Stmt = Let | Assign | Unpack | Return | Revert | If


@dataclass(frozen=True)
class Function:
    """`returns` is the type of the value, a tuple for several returns. `results` are the named return variables, one
    for each value or None for a value without a name, or empty when no value has a name: each starts at zero, and the
    function returns them when it ends without a return."""

    name: str
    params: tuple[Var, ...]
    returns: Type
    body: tuple[Stmt, ...]
    src: Src | None = None
    results: tuple[Var | None, ...] = ()

    @property
    def result(self) -> Var | None:
        """The named return variable of a function of one return."""
        return self.results[0] if len(self.results) == 1 else None

    @property
    def named(self) -> tuple[Var, ...]:
        return tuple(v for v in self.results if v is not None)


@dataclass(frozen=True)
class Read:
    """An input that the function reads from outside itself. `source` names the read as the read map does, such as
    `TOKEN.balanceOf(address(this))`, `tx.gasprice` or `_meter.frameGas`."""

    var: Var
    source: str


@dataclass(frozen=True)
class Effect:
    """A write, a call or an event that the model leaves out. `kind` is "write" (to storage), "call" (an external call,
    with the source text of each argument in `arguments`), "copy" (to a field of a memory
    copy of storage) or "event". `place` names what it writes as a read map names a read, such as `$info.rate` or
    `$balances[_who]`, or the event by its signature, such as `Updated(uint256)`. `source` is its text."""

    kind: str
    place: str
    source: str
    src: Src | None = None
    arguments: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"{self.src}: {self.source}"


@dataclass(frozen=True)
class Output:
    """A write or an external call argument that the model has as a value, a named return variable of the modeled
    function after its own values. `source` names it as the output map does: a storage place such as
    `$spenders[msg.sender].left`, or an external call such as `psm.buyGem(msg.sender,amt)` with `index` the argument.
    `before` is the input of its value before the call, for a write whose place the read map names too."""

    var: Var
    source: str
    kind: str
    index: int | None = None
    before: Var | None = None


@dataclass(frozen=True)
class Model:
    """`function` is the modeled function. Its parameters and the reads are the inputs. `callees` are the internal
    functions that it calls. `math` is the path that the emitted Solidity imports OpenZeppelin's Math from, when the
    model calls it, and `imports` are the other import directives that it needs. `pragma` is its version pragma and
    `spdx` its license identifier, each as the source file writes it. `stem`, if set, names the emitted contracts
    instead of the contract and function names (solidity.names). `effects` are the writes, external calls and events
    of the function and its callees, which the model leaves out. `outputs` are those it has as values, which the
    modeled function returns after its own."""

    contract: str
    function: Function
    reads: tuple[Read, ...] = ()
    callees: tuple[Function, ...] = ()
    math: str = ""
    imports: tuple[str, ...] = ()
    pragma: str = "^0.8.0"
    spdx: str = "UNLICENSED"
    stem: str = ""
    effects: tuple[Effect, ...] = ()
    outputs: tuple[Output, ...] = ()

    @property
    def name(self) -> str:
        return f"{self.contract}.{self.function.name}"

    @property
    def results(self) -> tuple[Type, ...]:
        """The types of the function's own values, which come before the outputs."""
        return parts(self.function.returns)[: len(parts(self.function.returns)) - len(self.outputs)]

    @property
    def inputs(self) -> tuple[Var, ...]:
        return self.function.params + tuple(read.var for read in self.reads)


def declared_locals(stmts):
    """Each local that the statements declare, in its branches too."""
    for stmt in stmts:
        if isinstance(stmt, Let):
            yield stmt.var
        elif isinstance(stmt, Unpack) and stmt.declare:
            yield from (v for v in stmt.vars if v is not None)
        elif isinstance(stmt, If):
            yield from declared_locals(stmt.then)
            yield from declared_locals(stmt.orelse)


def walk(node):
    """Every expression in a statement, a function or an expression, depth first."""
    if isinstance(node, Function):
        for stmt in node.body:
            yield from walk(stmt)
    elif isinstance(node, Let | Assign | Unpack | Return):
        yield from walk(node.expr)
    elif isinstance(node, Revert):
        return
    elif isinstance(node, If):
        yield from walk(node.cond)
        for stmt in node.then + node.orelse:
            yield from walk(stmt)
    else:
        yield node
        for arg in getattr(node, "args", ()):
            yield from walk(arg)
