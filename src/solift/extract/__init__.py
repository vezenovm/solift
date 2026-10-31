"""The extractor: reads the solc AST of one function and builds its model.

A target names a function as `path:Contract.function`, or as `path:Contract.function(MarketParams,Id,address)` with the
types of its parameters, for one of several functions of one name. The read map names each value that the function
reads from outside itself, and gives it an input name. A key is one of:

- a storage variable or immutable by its name (`$floor`, `TOKEN`), or a field of a storage struct (`$config.cap`),
  whether the function reads it directly or through a memory copy (`Config memory cfg = $config;` then `cfg.cap`);
- a field of a storage struct in a mapping, at a key that is a parameter of the modeled function or a field of `tx`,
  `block` or `msg`, through a memory copy or a storage pointer (`Rate storage rate = $rates[_kind];` then `rate.cap`,
  as `$rates[_kind].cap`), also a nested field (`rate.config.cap`, as `$rates[_kind].config.cap`) and a pointer passed
  to a callee;
- a field of a storage struct parameter of the modeled function (`_spender.left`);
- a field of a memory struct parameter of the modeled function (`_meter.used`);
- a field of `tx`, `block` or `msg` (`tx.gasprice`, `block.basefee`);
- the balance of an address (`address(this).balance`), which no external call that can change state may precede;
- any other expression, such as an external view call, by its source text without whitespace
  (`TOKEN.balanceOf(address(this))`). An internal call in the read map is a cut point: its result is an input.

A read map entry that the function does not read is an error, except a field of `tx`, `block` or `msg`: every
transaction has it, so it is an input even where the function ignores it, and a theorem can name it. A key names one
value for the whole call, so a local in it, also below a pointer, must be one that only its declaration assigns, the
same one at each read of the key, in the modeled function or in a callee that runs once.

Internal calls are inlined, also a library call on a value (`x.mulDivDown(y, d)`): each callee becomes a function of the
model. OpenZeppelin's `Math.min`, `Math.max`, and `Math.mulDiv` with or without `Math.Rounding.Floor`, become operators
of the table in ops.py. A constant becomes its value, with its name and declared type, and an enum member its index. A
contract or interface value is its address, and `address(x)` of one is the same value. `uint<n>(x)` of an unsigned x
widens it, or keeps its low n bits. Locals can be assigned, also in branches, and a named return starts at zero. A
function can return several values, and a tuple declaration or assignment (`(, uint256 y) = limits(a, b);`,
`(x, y) = (y, x);`) takes them apart into locals. `type(uint<n>).max` and `.min` are constants. `require`, `assert`
and `revert` revert. A parameter of a user-defined value type, such as Morpho's `Id`, or of a signed integer type,
that the function uses only inside read map keys or as an argument of an effect, is not an input. Anything else stops
the extraction with an error that names the construct and its place in the source.

The model is the function's value and whether it reverts, which is what the link compares, and its outputs: the writes
and the external call arguments that the output map names, as `{"$total": "total"}` or
`{"psm.buyGem(msg.sender,amt)": (None, "amount")}`. An output's value can revert. A write of an output in some paths
only needs its place in the read map, as its value before the call, and a read of its place after the write reads the
output. The model leaves out the other writes, external calls and events (`Model.effects`), so the extractor refuses
one that could change the value or an output:

- a written value, a written place, an argument of an external call or an event argument that is no output must not
  revert: it is a local, a parameter, a constant, a field of `tx`, `block` or `msg`, storage, a memory copy, a mapping
  entry, a field of one of these, or a conversion of one of these to an unsigned integer or an address. A compound
  assignment such as `+=` to storage is refused, unless it is to an output.
- after a write, the function must not read what the write changed: the storage variable it wrote or the memory copy it
  wrote a field of, also through a callee, and it must not make a call in the read map that is not to an internal pure
  function, which could read the changed storage. An external call that can change state can change any storage, so no
  read of storage may follow it.
- a write to the storage variable of an output that is not the output's place is refused, as it could change it, and so
  is an output in a callee. Two outputs of one storage variable must differ at a field, as `$a[k].left` and
  `$a[k].last`: at two keys, they are one place where the keys are equal.
- a call to a function that writes storage must be the whole value of its statement, as solc does not fix the order of
  the parts of an expression.
- a call in the read map must be to a view or pure function.

The model assumes that each read succeeds, and that each external call succeeds and does not call back into the
contract: an external view call that reverts has no input in the model.

The package builds the model one part of the IR at a time, each part a mixin of the extractor: statements.py the
functions and their statements, expressions.py the expressions and the inlined callees, reads.py the inputs and the
rules of read map keys, effects.py the effects and the outputs, and values.py the constants and types. extractor.py
holds the state of one extraction, which the parts share, and checks the finished model.
"""

from solift.extract.errors import ExtractionError, Missing, Wanted
from solift.extract.extractor import Extraction, declared, extract

__all__ = ["Extraction", "ExtractionError", "Missing", "Wanted", "declared", "extract"]
