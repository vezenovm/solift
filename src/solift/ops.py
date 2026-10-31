"""The operator table: the trusted link between the Solidity back end and the z3 back end.

    op       Solidity               z3 value            reverts when                     outside
    add      a + b                  a + b               a + b >= 2**256
    sub      a - b                  a - b               a < b
    mul      a * b                  a * b               a * b >= 2**256
    div      a / b                  a / b (floor)       b == 0
    mulDiv   Math.mulDiv(x, y, d)   x * y / d (floor)   d == 0 or x * y / d >= 2**256    x >= 2**128 or y >= 2**128
    min      Math.min(a, b)         If(a < b, a, b)     never
    lt ... ne                       a < b ... a != b    never
    not      !a                     Not(a)              never
    and, or  a && b, a || b         And(a, b), Or(a, b) never
    ite      c ? a : b              If(c, a, b)         never
    widen    uint<n>(a)             a                   never
    narrow   uint<n>(a)             a % 2**n            never

An expression also reverts when an operand that runs reverts, and is outside when such an operand is outside. `and`
and `or` skip their second operand, and `ite` the branch not taken (smt.py).

Arithmetic is at uint256: `make` refuses an addition, subtraction, multiplication or division at a narrower type, so
the overflow bound is always 2**256. `widen` converts an unsigned value to a type at least as wide, as in
`uint256(a) * b` for two uint128 operands, and `narrow` to a narrower type, which keeps the low bits, as Solidity's
explicit conversion does: `uint192(a)` of a uint256 is `a % 2**192`. Each takes the type to convert to. Comparisons
and library calls take operands of any unsigned width. `eq` and `ne` also compare two bools, two addresses, or two
members of one enum, and `ite` selects between two values of one such type. z3 sees an address or an enum member as its
integer value.

Halmos proves on the bytecode each row that can revert, and the two OpenZeppelin facts that the min and mulDiv rows rest
on (`PROOFS`): `Math.min(a, b)` is `a < b ? a : b`, and `Math.mulDiv(x, y, d)` is `x * y / d` when `x` and `y` are below
2**128. A model's `outside` is the inputs where a mulDiv operand reaches 2**128, where the second fact is not proved.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import z3

from solift.ir import BOOL, UINT256, Expr, Op, Src, Type, uint

U256 = 2**256
HALF = 2**128


@dataclass(frozen=True)
class Operator:
    """`solidity` formats the operand texts. `kind` sets how `make` types the operation: arithmetic, library (a call
    to OpenZeppelin's Math), compare, logic, select or convert."""

    name: str
    arity: int
    solidity: str
    kind: str
    value: Callable
    reverts: Callable | None = None
    outside: Callable | None = None
    proofs: tuple[str, ...] = ()


TABLE = {
    op.name: op
    for op in [
        Operator(
            "add",
            2,
            "{0} + {1}",
            "arithmetic",
            lambda a, b: a + b,
            reverts=lambda a, b: a + b >= U256,
            proofs=("check_row_addRevertsExactlyOnOverflow",),
        ),
        Operator(
            "sub",
            2,
            "{0} - {1}",
            "arithmetic",
            lambda a, b: a - b,
            reverts=lambda a, b: a < b,
            proofs=("check_row_subRevertsExactlyBelowZero",),
        ),
        Operator(
            "mul",
            2,
            "{0} * {1}",
            "arithmetic",
            lambda a, b: a * b,
            reverts=lambda a, b: a * b >= U256,
            proofs=("check_row_mulRevertsExactlyOnOverflow",),
        ),
        Operator(
            "div",
            2,
            "{0} / {1}",
            "arithmetic",
            lambda a, b: a / b,
            reverts=lambda a, b: b == 0,
            proofs=("check_row_divRevertsExactlyOnZero",),
        ),
        Operator(
            "mulDiv",
            3,
            "Math.mulDiv({0}, {1}, {2})",
            "library",
            lambda x, y, d: x * y / d,
            reverts=lambda x, y, d: z3.Or(d == 0, x * y / d >= U256),
            outside=lambda x, y, d: z3.Or(x >= HALF, y >= HALF),
            proofs=(
                "check_row_mulDivOfHalfWidthOperandsRevertsExactlyOnZero",
                "check_lemma_mulModOfHalfWidthOperandsIsTheProduct",
                "check_lemma_mulDivOfHalfWidthOperandsIsTheFloor",
            ),
        ),
        Operator(
            "min",
            2,
            "Math.min({0}, {1})",
            "library",
            lambda a, b: z3.If(a < b, a, b),
            proofs=("check_lemma_minIsTheSmallerValue",),
        ),
        Operator("lt", 2, "{0} < {1}", "compare", lambda a, b: a < b),
        Operator("le", 2, "{0} <= {1}", "compare", lambda a, b: a <= b),
        Operator("gt", 2, "{0} > {1}", "compare", lambda a, b: a > b),
        Operator("ge", 2, "{0} >= {1}", "compare", lambda a, b: a >= b),
        Operator("eq", 2, "{0} == {1}", "compare", lambda a, b: a == b),
        Operator("ne", 2, "{0} != {1}", "compare", lambda a, b: a != b),
        Operator("not", 1, "!{0}", "logic", z3.Not),
        Operator("and", 2, "{0} && {1}", "logic", z3.And),
        Operator("or", 2, "{0} || {1}", "logic", z3.Or),
        Operator("ite", 3, "{0} ? {1} : {2}", "select", z3.If),
        Operator("widen", 1, "{type}({0})", "convert", lambda a: a),
        Operator("narrow", 1, "{type}({0})", "convert", None),
    ]
}


def common_type(a: Type, b: Type) -> Type | None:
    """Solidity's type for two unsigned operands: the wider type, and a literal takes the other operand's type. None
    for two literals, which solc folds at compile time."""
    if a.kind == "literal":
        return None if b.kind == "literal" else b
    if b.kind == "literal":
        return a
    return a if a.bits >= b.bits else b


def literal_type(value: int) -> Type:
    """The smallest unsigned type that holds a literal, which solc gives to a literal that has no other type."""
    return uint(max(8, -(-value.bit_length() // 8) * 8))


def make(op: str, *args: Expr, src: Src | None = None, to: Type = UINT256) -> Op:
    """An operation, with its result type. A conversion converts to `to`. Raises TypeError for an operation that the
    table does not cover."""
    operator = TABLE.get(op)
    where = f" at {src}" if src else ""
    if operator is None:
        raise TypeError(f"{op}{where} is not in the operator table")
    if len(args) != operator.arity:
        raise TypeError(f"{op}{where} takes {operator.arity} operands, not {len(args)}")
    types = [arg.type for arg in args]
    if operator.kind == "arithmetic":
        _numbers(op, types, where)
        result = common_type(*types)
        if result is None:
            raise TypeError(f"{op}{where} of two literals: solc folds it, and the extractor does not")
        if result.bits != 256:
            raise TypeError(f"{op}{where} at {result.solidity}: arithmetic narrower than uint256 is not supported")
    elif operator.kind == "library":
        _numbers(op, types, where)
        result = UINT256
    elif operator.kind == "compare":
        if op in ("eq", "ne") and _same_kind(*types):
            result = BOOL
        else:
            _numbers(op, types, where)
            if common_type(*types) is None:
                raise TypeError(f"{op}{where} of two literals: solc folds it, and the extractor does not")
            result = BOOL
    elif operator.kind == "convert":
        _numbers(op, types, where)
        if to.kind != "uint":
            raise TypeError(f"{op}{where} converts to an unsigned type, not {to.kind}")
        if types[0].kind == "uint" and (types[0].bits <= to.bits) != (op == "widen"):
            raise TypeError(f"{op}{where} of a {types[0].solidity} to {to.solidity}")
        result = to
    elif operator.kind == "logic":
        if any(t != BOOL for t in types):
            raise TypeError(f"{op}{where} takes bool operands")
        result = BOOL
    else:
        if types[0] != BOOL:
            raise TypeError(f"{op}{where} needs a bool condition")
        if _same_kind(types[1], types[2]):
            result = types[1]
        else:
            # A conditional takes the type that holds both branches, a literal by its value: solc types
            # `flag ? x : 2**255` as uint256 for a uint8 x.
            _numbers(op, types[1:], where)
            widths = [literal_type(arg.value) if arg.type.kind == "literal" else arg.type for arg in args[1:]]
            result = widths[0] if widths[0].bits >= widths[1].bits else widths[1]
    return Op(op, tuple(args), result, src)


def _same_kind(a: Type, b: Type) -> bool:
    """Two bools, two addresses, or two members of one enum."""
    return a == b and a.kind in ("bool", "address", "enum")


def _numbers(op: str, types: list[Type], where: str) -> None:
    if not all(t.is_number for t in types):
        raise TypeError(f"{op}{where} takes unsigned operands, not {', '.join(t.kind for t in types)}")


@dataclass(frozen=True)
class Proof:
    """A Halmos check of one row or lemma. A row check calls `wrapper`, an external function of the generated
    CheckedOperators contract, so that it checks the operator as compiled code. A lemma has no wrapper."""

    name: str
    check: str
    wrapper: str = ""


_MULMOD = """\
    uint256 mm;
    assembly {
      mm := mulmod(_x, _y, not(0))
    }
"""

PROOFS = {
    proof.name: proof
    for proof in [
        Proof(
            "check_row_addRevertsExactlyOnOverflow",
            wrapper="""\
  function add(uint256 _a, uint256 _b) external pure returns (uint256) {
    return _a + _b;
  }
""",
            check="""\
  function check_row_addRevertsExactlyOnOverflow(uint256 _a, uint256 _b) public view {
    (bool ok, bytes memory out) = address(ops).staticcall(abi.encodeCall(ops.add, (_a, _b)));
    uint256 sum;
    unchecked {
      sum = _a + _b;
    }
    assert(ok == (sum >= _a));
    assert(!ok || abi.decode(out, (uint256)) == sum);
  }
""",
        ),
        Proof(
            "check_row_subRevertsExactlyBelowZero",
            wrapper="""\
  function sub(uint256 _a, uint256 _b) external pure returns (uint256) {
    return _a - _b;
  }
""",
            check="""\
  function check_row_subRevertsExactlyBelowZero(uint256 _a, uint256 _b) public view {
    (bool ok, bytes memory out) = address(ops).staticcall(abi.encodeCall(ops.sub, (_a, _b)));
    uint256 difference;
    unchecked {
      difference = _a - _b;
    }
    assert(ok == (_a >= _b));
    assert(!ok || abi.decode(out, (uint256)) == difference);
  }
""",
        ),
        Proof(
            "check_row_mulRevertsExactlyOnOverflow",
            wrapper="""\
  function mul(uint256 _a, uint256 _b) external pure returns (uint256) {
    return _a * _b;
  }
""",
            check="""\
  function check_row_mulRevertsExactlyOnOverflow(uint256 _a, uint256 _b) public view {
    (bool ok, bytes memory out) = address(ops).staticcall(abi.encodeCall(ops.mul, (_a, _b)));
    uint256 product;
    unchecked {
      product = _a * _b;
    }
    assert(ok == (_a == 0 || product / _a == _b));
    assert(!ok || abi.decode(out, (uint256)) == product);
  }
""",
        ),
        Proof(
            "check_row_divRevertsExactlyOnZero",
            wrapper="""\
  function div(uint256 _a, uint256 _b) external pure returns (uint256) {
    return _a / _b;
  }
""",
            check="""\
  function check_row_divRevertsExactlyOnZero(uint256 _a, uint256 _b) public view {
    (bool ok, bytes memory out) = address(ops).staticcall(abi.encodeCall(ops.div, (_a, _b)));
    assert(ok == (_b != 0));
    if (ok) {
      assert(abi.decode(out, (uint256)) == _a / _b);
    }
  }
""",
        ),
        Proof(
            "check_row_mulDivOfHalfWidthOperandsRevertsExactlyOnZero",
            wrapper="""\
  function mulDiv(uint256 _x, uint256 _y, uint256 _d) external pure returns (uint256) {
    return Math.mulDiv(_x, _y, _d);
  }
""",
            check=f"""\
  function check_row_mulDivOfHalfWidthOperandsRevertsExactlyOnZero(uint128 _x, uint128 _y, uint256 _d) public view {{
{_MULMOD}    // check_lemma_mulModOfHalfWidthOperandsIsTheProduct proves this assumption.
    vm.assume(mm == uint256(_x) * uint256(_y));
    (bool ok,) = address(ops).staticcall(abi.encodeCall(ops.mulDiv, (_x, _y, _d)));
    assert(ok == (_d != 0));
  }}
""",
        ),
        Proof(
            "check_lemma_mulModOfHalfWidthOperandsIsTheProduct",
            check=f"""\
  function check_lemma_mulModOfHalfWidthOperandsIsTheProduct(uint128 _x, uint128 _y) public pure {{
{_MULMOD}    assert(mm == uint256(_x) * uint256(_y));
  }}
""",
        ),
        Proof(
            "check_lemma_mulDivOfHalfWidthOperandsIsTheFloor",
            check=f"""\
  function check_lemma_mulDivOfHalfWidthOperandsIsTheFloor(uint128 _x, uint128 _y, uint256 _d) public pure {{
    vm.assume(_d > 0);
    uint256 product = uint256(_x) * uint256(_y);
{_MULMOD}    // check_lemma_mulModOfHalfWidthOperandsIsTheProduct proves this assumption.
    vm.assume(mm == product);
    assert(Math.mulDiv(_x, _y, _d) == product / _d);
  }}
""",
        ),
        Proof(
            "check_lemma_minIsTheSmallerValue",
            check="""\
  function check_lemma_minIsTheSmallerValue(uint256 _a, uint256 _b) public pure {
    assert(Math.min(_a, _b) == (_a < _b ? _a : _b));
  }
""",
        ),
    ]
}
