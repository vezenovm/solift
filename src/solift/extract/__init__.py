"""The extractor: reads the solc AST of one function and builds its model.

A target is `path:Contract.function`, with parameter types (`path:Contract.function(MarketParams,Id,address)`) to pick
one of several functions of one name. The read map gives each value read from outside the function an input name. A
key is one of:

- a storage variable, immutable or storage struct field (`$floor`, `TOKEN`, `$config.cap`), read directly or through
  a memory copy;
- a struct field in a mapping at a key that is a parameter or a field of `tx`, `block` or `msg`, read through a copy
  or a storage pointer, at any depth and in callees (`$rates[_kind].config.cap`);
- a field of a storage or memory struct parameter (`_spender.left`, `_meter.used`);
- a field of `tx`, `block` or `msg` (`tx.gasprice`);
- a balance (`address(this).balance`), read before any external call that can change state;
- any other expression by its source text without whitespace (`TOKEN.balanceOf(address(this))`). An internal call in
  the read map is a cut point: its result is an input.

An entry that the function does not read is an error, except a field of `tx`, `block` or `msg`, which is always an
input. A key names one value for the whole call, so each local in it is assigned only by its declaration, is the same
local at each read, and belongs to the modeled function or to a callee that runs once.

Internal and library calls are inlined. A call of a contract's function runs the function that the linearization of
the target's contract gives it: the most derived override, or for `super.f()` the next one after the calling contract.
`Math.min`, `Math.max` and `Math.mulDiv` rounded down are operators of ops.py. A constant is its value, an enum member
its index, and a contract its address. `uint<n>(x)` widens x or keeps its low n bits. Locals can be reassigned, also in
branches, and a named return starts at zero. Several returns, tuple declarations and assignments, `type(uint<n>).max`
and `.min`, `require`, `assert` and `revert` are supported. A parameter of a user-defined value type or a signed type
that only read map keys or effects use is not an input.
Anything else stops the extraction with an error that names the construct and its place.

The model is the function's value, whether it reverts, and its outputs: the writes and external call arguments that
the output map names (`{"$total": "total"}`, `{"psm.buyGem(msg.sender,amt)": (None, "amount")}`). An output's value
can revert. An output written on only some paths needs its place in the read map, as its value before the call, and a
read of its place after the write reads the output. The model leaves out the other writes, external calls and events
(`Model.effects`), so the extractor refuses one that could change the value or an output:

- a left-out write's value or place, or an argument of a left-out call or event, must not revert. A compound
  assignment such as `+=` to storage is refused unless it is to an output.
- after a write, nothing may read what it changed, also through a callee, and no read map call may follow unless it is
  to an internal pure function. No storage read may follow an external call that can change state.
- a write to an output's storage variable at another place is refused, and so is an output in a callee. Two outputs
  of one storage variable must differ at a field.
- a call that writes storage must be the whole value of its statement, as solc does not fix the order of evaluation.
- a call in the read map must be to a view or pure function.

The model assumes that each read succeeds, and that each external call succeeds and does not call back into the
contract.

Each part of the IR has its own mixin of `_Extractor`: statements.py, expressions.py (with inlined callees), reads.py
(inputs and key rules), effects.py (effects and outputs) and values.py (constants and types). extractor.py holds the
shared state and checks the finished model.
"""

from solift.extract.errors import ExtractionError, Missing, Wanted
from solift.extract.extractor import Extraction, declared, extract

__all__ = ["Extraction", "ExtractionError", "Missing", "Wanted", "declared", "extract"]
