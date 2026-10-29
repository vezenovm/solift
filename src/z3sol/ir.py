"""The IR: one loop-free Solidity function as typed expressions and statements.

A model is a function whose reads from outside it (storage, external view calls, `tx` and `block` fields) are inputs,
like its parameters. smt.py turns a model into z3 integer terms, and solidity.py into pure Solidity. ops.py holds the
operator table that ties the two together and checks the types of each operation.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    """`uint<bits>`, `bool`, or `literal`: a number literal, which takes the type of the operand next to it."""

    kind: str
    bits: int = 0

    @property
    def solidity(self) -> str:
        if self.kind == "uint":
            return f"uint{self.bits}"
        if self.kind == "bool":
            return "bool"
        raise ValueError(f"a {self.kind} has no Solidity type of its own")

    @property
    def bound(self) -> int:
        """Values of an unsigned type lie in [0, bound)."""
        if self.kind != "uint":
            raise ValueError(f"{self.kind} has no bound")
        return 2**self.bits

    @property
    def is_number(self) -> bool:
        return self.kind in ("uint", "literal")


def uint(bits: int) -> Type:
    if bits % 8 or not 8 <= bits <= 256:
        raise ValueError(f"no type uint{bits}")
    return Type("uint", bits)


UINT256 = uint(256)
BOOL = Type("bool")
LITERAL = Type("literal")


class Expr:
    type: Type
    src: Src | None


@dataclass(frozen=True, eq=False)
class Var(Expr):
    """A parameter, a local, or a read. `input` marks an input of the model: a parameter of the modeled function or a
    read. A parameter of a called function is not an input."""

    name: str
    type: Type = UINT256
    input: bool = False
    src: Src | None = None


@dataclass(frozen=True, eq=False)
class Const(Expr):
    """A literal (`text` as written, such as `1e18`), or a named constant (`name`, such as `MAX_PRIORITY_FEE_WEI`,
    with its declared type)."""

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
    """A call to an internal function of the model, by name."""

    function: str
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
class Return:
    expr: Expr
    src: Src | None = None


@dataclass(frozen=True)
class If:
    cond: Expr
    then: tuple[Stmt, ...]
    orelse: tuple[Stmt, ...] = ()
    src: Src | None = None


Stmt = Let | Return | If


@dataclass(frozen=True)
class Function:
    name: str
    params: tuple[Var, ...]
    returns: Type
    body: tuple[Stmt, ...]
    src: Src | None = None


@dataclass(frozen=True)
class Read:
    """An input that the function reads from outside itself. `source` names the read as the read map does, such as
    `TOKEN.balanceOf(address(this))` or `tx.gasprice`."""

    var: Var
    source: str


@dataclass(frozen=True)
class Model:
    """`function` is the modeled function. Its parameters and the reads are the inputs. `callees` are the internal
    functions that it calls. `imports` are the import directives that the emitted Solidity needs, `pragma` its version
    pragma and `spdx` its license identifier, each as the source file writes it."""

    contract: str
    function: Function
    reads: tuple[Read, ...] = ()
    callees: tuple[Function, ...] = ()
    imports: tuple[str, ...] = ()
    pragma: str = "^0.8.0"
    spdx: str = "UNLICENSED"

    @property
    def name(self) -> str:
        return f"{self.contract}.{self.function.name}"

    @property
    def inputs(self) -> tuple[Var, ...]:
        return self.function.params + tuple(read.var for read in self.reads)


def walk(node):
    """Every expression in a statement, a function or an expression, depth first."""
    if isinstance(node, Function):
        for stmt in node.body:
            yield from walk(stmt)
    elif isinstance(node, Let | Return):
        yield from walk(node.expr)
    elif isinstance(node, If):
        yield from walk(node.cond)
        for stmt in node.then + node.orelse:
            yield from walk(stmt)
    else:
        yield node
        for arg in getattr(node, "args", ()):
            yield from walk(arg)
