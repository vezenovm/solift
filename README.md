# solift

solift proves arithmetic properties of Solidity functions, such as rounding, solvency, and fee and pricing bounds,
over exact integers, and proves with Halmos that what it proved on is the contract's bytecode.

For each function of a spec, it:

1. extracts an integer model from the solc AST. Each value that the function reads from outside itself (storage, a
   mapping entry, an external view call, `tx` and `block` fields, the fields of a struct parameter, or the result of an
   internal call that the spec cuts) becomes a named input, through a read map;
2. emits the model as pure Solidity, and proves with Halmos that this Solidity and the contract's bytecode give the same
   result and revert on the same inputs. Both sides have the same structure, so Halmos checks an equivalence, not the
   property;
3. proves theorems, written in Python, on the model's integer terms, with z3, and with yices and cvc5 when z3 cannot
   decide.

Nobody writes the formula. People write the theorems, the read map, and the test harness with its read adapters.

## What it found

At the commit of Resupply's lending pair that the exploit of 2025-06-26 drained, the theorem "a borrower that the pair
finds solvent has debt below twice the loan-to-value limit of its collateral's value" fails:

```
COUNTEREXAMPLE WITNESS [solvent-debt-within-twice-the-ltv] a borrower that the pair finds solvent has debt below twice
  (maxLTV + 1) / LTV_PRECISION of the collateral's value at the oracle price [0.03s]
  _borrower=0 price=1500000000000024999999999999999999488 storedRate=0 maxLTV=3 collateral=2 borrowed=240000000000004
PROVED WITNESS [solvent-debt-within-twice-the-ltv-up-to-a-price-of-1e36] up to an oracle price of 1e36, ... [0.01s]
vectors: 192 of 192 agree
PASS ResupplyPairHarnessSolventAfterUpdateLink.check_link [0.74s]
```

An oracle price above 1e36 rounds the exchange rate down to 0, and at a rate of 0 one wei of collateral backs any debt.
On the fixed source (resupplyfi/resupply@d84b3bf631) the theorem proves. This is the arithmetic failure behind the
exploit, not the transaction: the harness runs `_updateExchangeRate` then `_isSolvent`, that the solvency check reads
the rate that `_updateExchangeRate` stored is a hypothesis, and the borrower's debt is a cut point.

The same theorem proves on Morpho Blue v1.0.0's `_isHealthy`, which takes the oracle price as it is. CI runs both at
their pinned commits, Resupply also on its fix, each with mutants that a theorem must reject (`acceptance/`).

## Install

Needs Python 3.12 or later, forge, and Linux or macOS.

```sh
uv tool install solift      # or: pipx install solift
```

Specs are Python modules, so the tool's environment needs the packages they import: `uv tool install solift --with
<package>`, or `pipx inject solift <package>`. To pin a commit: `pip install
git+https://github.com/vezenovm/solift@<commit>`. `SOLIFT_FORGE` names the forge binary, for a toolchain that ships it
under another name.

## Use

```sh
solift reads src/Vault.sol:Vault.previewRedeem --root .   # the read map that a function needs, and a link skeleton
solift run vault_specs.spec --root .                      # the specs of a module: theorems, vectors and the link
```

```python
import pathlib

from solift import Harness, Link, Project, Spec

REDEEM = Spec(
    name="vault-redeem",
    target="src/Vault.sol:Vault.previewRedeem",
    reads={"ASSET.balanceOf(address(this))": "held", "totalSupply": "supply"},
    link=Link(
        setup=Harness(pathlib.Path(__file__).parent / "VaultSetup.sol", "VaultSetup"),
        call="vault.previewRedeem(_shares)",
        adapters={"held": "asset.set(held);", "supply": "vault.setSupply(supply);"},
    ),
    project=Project.foundry(),
)


@REDEEM.theorem(assume=("no-revert", "inside"), witness=lambda q: q.result > 0)
def redeem_rounds_down(q):
    """a redeem pays at most the shares' exact part of the assets, shares * (held + 1) / (supply + 1)"""
    return q.result * (q.supply + 1) <= q._shares * (q.held + 1)
```

[USAGE.md](USAGE.md) walks through a new contract. [examples/vault](examples/vault) is this spec in a standalone
Foundry project, as a project that uses solift lays out its own. Your project is read-only: a run builds in an overlay
in its workspace ([docs/overlay.md](docs/overlay.md)).

## What a run proves

- The link: for every input, the contract and the emitted Solidity both succeed with the same result, or both revert.
  Each input is a parameter of the Halmos check, set in the contract through the call or a read adapter, except an
  `observed` input, which the check reads from the deployed contract: `tx.gasprice`, which Halmos already makes
  symbolic, a cut point, which takes the values that its own symbolic inputs give it, and an immutable that the
  harness sets, such as a token address, which takes only that value. The check reads the inputs before the call.
  Revert data, storage writes and events are not compared.
- The operator rows and lemmas that the model uses, proved in the same build: each checked operator reverts exactly
  when the table says, `Math.min` is the smaller value, and `Math.mulDiv` is the floor of the product when both
  operands are below 2**128.
- The theorems, each with its assumptions listed: the input types, the range of each free variable of a bounded kind,
  and, when stated, that the model does not revert or is inside the lemma domain. A theorem expected to prove needs a
  witness that its hypotheses hold. An expected counterexample names its issue or reason. A counterexample and a
  witness give a value for every input and every free variable.

Each theorem's record also says how far its result reaches:

- `lemma_domain`: whether the proof rests on `Math.mulDiv` where an operand reaches 2**128, which the build does not
  prove. It is `established` when the hypotheses keep every run that the proof makes inside the domain, with each
  argument in the range of its type, `not-established` or `unknown` when they do not, and `not-used` without mulDiv.
- `callees`: the functions that the theorem runs on its own arguments with `q.call`. Such a theorem is a theorem of the
  integer model: the link covers those functions only where the modeled function calls them.
- `link`: whether the spec's link passed, failed, or did not run.

A link check fails on anything but PASS, on a path stopped at the loop bound or at Halmos's `--depth` or `--width`
limit, on a missing or undeclared check, on a result from a file other than the generated one, on two results for one
check, when Halmos exits with a failure, and when Halmos skips a contract. "Unknown" from every solver fails a theorem.

A cut point, such as `_price()`, is an input of the model. The link sets it through the harness, such as a price feed
with a symbolic answer, so it proves the model equal to the contract only for the values the harness can produce.

A run builds the project once, and runs every stage even when one fails. It prints one line for each result, and writes
a JSON record (`schema` 3) with each result and what it rests on: the sources of each model with their hashes, the
versions of solc, OpenZeppelin, Halmos, yices, z3, cvc5, forge, Python and solift (with its commit), and the sha256 of
each solc input of the build, of each theorem's query, of the spec's Python files and of each file that the overlay
adds. The run fails when any result is not the expected one.

## What it trusts

The integer translation in `smt.py` and the operator table (the vectors check them on samples), Halmos's EVM model, the
solvers, the harness and its read adapters, and the compiler. A read of an external view call, and a cut point, is
assumed to succeed.

## What it takes

| Takes | Refuses, with the place in the source |
|---|---|
| checked `uint` arithmetic, comparisons, boolean logic, ternaries | `unchecked`, signed integers, `**`, `%`, assembly |
| `if`, early returns, `require`, `assert`, `revert` | loops |
| locals, also assigned in branches; compound assignments; named returns | tuple returns, modifiers |
| addresses, enums, constants, immutables, `uint256(x)` of a narrower `x` | narrowing casts, arithmetic below `uint256` |
| internal calls (inlined), library calls on a value (`x.mulDivDown(y, d)`), named arguments | a call that writes storage inside a larger expression |
| `Math.min`, `Math.mulDiv`, as operators backed by lemmas | other `Math` functions |
| storage writes and events that no later read sees (listed, not modeled) | a write that a later read sees |
| one of several overloads, by its parameter types | a variable or constant named `i`, a local that shadows another |

The type of each operation must match the type in solc's AST. To cut out a call that does not fit, name it in the read
map: its result becomes an input.

## How it compares

solift is a small, open, inspectable workflow with a stated trust base, for one function's arithmetic. It does not
claim more power than these tools:

- **Halmos** runs symbolic tests on bytecode, with 256-bit bitvector arithmetic. solift uses it for the equivalence of a
  model of the same structure, and for the operator lemmas, and proves the property on unbounded integers.
- **hevm** checks bytecode equivalence too. solift adds the extraction of the model and the integer theorems.
- **Certora** has `mathint`, summaries, storage and invariants across calls, in its own spec language. solift covers
  one call, with theorems in Python.
- **Kontrol** proves Foundry specifications in K, with lemmas and loop invariants.
- **SMTChecker** is part of the compiler and reasons across transactions and loops, with no link to the bytecode.

## Roadmap

Coverage, by how much it widens what solift takes: generated names that never clash with the source's (no refusal of
`i`, no clash of two callees' names), constant expressions, arithmetic below `uint256`, `%`, and library summaries
through the operator table, each with its lemma. Then, as projects need them: `unchecked`, tuple returns, bounded
loops.

Past arithmetic, one step at a time:

1. **State transitions.** A storage write becomes an output of the model, named as the read map names its read
   (`$totalAssets` gives `post.totalAssets`). The link reads each written place after the call through the harness and
   compares it, and theorems name `q.post.totalAssets`: conservation, monotonicity, exact deltas. The extractor already
   records each write with its place (`Model.effects`).
2. **Invariants over a set of functions.** With writes named as reads, an invariant I holds when, for each function f
   of a set, I before f and f not reverting give I after f. Two reads of one mapping at keys that may be equal are the
   hard part.
3. **Events**, as outputs that the link compares.

## Develop

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e . -r requirements-dev.txt
git submodule update --init
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
.venv/bin/solift run examples.quoter.spec                   # one spec on the test fixtures
.venv/bin/python -m acceptance.resupply --root <resupply>   # setup in acceptance/resupply/__main__.py
.venv/bin/python -m acceptance.morpho --root <morpho-blue>  # setup in acceptance/morpho/__main__.py
```

`tests/test_boundary.py` fails when the package imports anything other than the standard library, itself and its
pinned dependencies, and when a Python file names a path in a home directory.

## License

Apache-2.0. See `LICENSE`.
