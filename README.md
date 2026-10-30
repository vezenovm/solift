# solift

solift proves arithmetic properties of Solidity functions, such as rounding, solvency, and fee and pricing bounds,
over exact integers, and proves with Halmos that the model it proves them on matches the bytecode.

For each function of a spec, solift:

1. extracts an integer model from the solc AST, with each outside read (storage, an external view call, a `block`
   field, a cut internal call) as a named input;
2. emits the model as pure Solidity, and proves with Halmos that it returns and reverts as the contract does. Both
   sides share their structure, so Halmos checks an equivalence, not the property;
3. proves your theorems, written in Python, on the model with z3, and with yices and cvc5 when z3 cannot decide.

You write the theorems, the read map and a test harness. Nobody writes the model.

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
On the fix (resupplyfi/resupply@d84b3bf631) the theorem proves. This reproduces the arithmetic failure behind the
exploit, not the transaction: that the solvency check reads the rate that `_updateExchangeRate` stored is a
hypothesis, and the borrower's debt is a cut point.

On Morpho Blue v1.0.0's `_isHealthy`, the tighter bound proves: debt at most the loan-to-value limit of the
collateral's value. CI runs both, with mutants that a theorem must reject (`acceptance/`).

## Install

Needs Python 3.12 or later, forge, and Linux or macOS.

```sh
uv tool install solift      # or: pipx install solift
```

Add the packages that your specs import: `uv tool install solift --with <package>`, or `pipx inject solift <package>`.

## Use

```sh
solift reads src/Vault.sol:Vault.previewRedeem --root .   # the read map that a function needs, and a link skeleton
solift run vault_specs.spec --root .                      # the specs of a module: theorems, vectors and the link
```

```python
import pathlib

from solift import Harness, Link, Project, Spec

SPEC = Spec(
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


@SPEC.theorem(assume=("no-revert", "inside"), witness=lambda q: q.result > 0)
def redeem_rounds_down(q):
    """a redeem pays at most the shares' exact part of the assets, shares * (held + 1) / (supply + 1)"""
    return q.result * (q.supply + 1) <= q._shares * (q.held + 1)
```

[USAGE.md](USAGE.md) walks through a new contract, and [examples/vault](examples/vault) holds this spec in a
standalone Foundry project. A run builds in an overlay in its workspace (`build/<spec>` by default), and leaves the
rest of your project untouched ([docs/overlay.md](docs/overlay.md)).

## What a run proves

- **The link**: on every input that the harness can produce, the contract and the emitted Solidity return the same
  result, or both revert. Revert data, storage writes and events are not compared.
- **The lemmas** that the model uses, in the same build: each checked operator reverts exactly when the table says,
  and `Math.mulDiv(x, y, d)` is `x * y / d` rounded down when `x` and `y` are below 2**128.
- **The theorems**, each with its assumptions listed, starting with the input types. A theorem that is expected to
  prove needs a witness, so that its hypotheses can hold, and an expected counterexample needs a reason.

Each theorem's record also says how far it reaches: `lemma_domain` (whether its hypotheses keep mulDiv where the lemma
is proved), `callees` (functions that it runs with `q.call` on its own arguments, which the link covers only where the
target calls them), and `link`. The run
writes a JSON record of every result, the tool versions, and the hashes of what each result rests on. It fails when a
result is not the expected one. [USAGE.md](USAGE.md#reference) has the details.

## What it trusts

The integer translation and the operator table (the vectors check them on samples), Halmos, the solvers, the compiler,
and your harness. External view calls and cut points are assumed not to revert.

## What it takes

| Takes | Refuses, with the place in the source |
|---|---|
| checked `uint` arithmetic, comparisons, boolean logic, ternaries | `unchecked`, signed integers, `**`, `%`, assembly |
| `if`, early returns, `require`, `assert`, `revert` | loops |
| locals, also assigned in branches; compound assignments; named returns | tuple returns, modifiers |
| addresses, enums, constants, immutables, `uint256(x)` of a narrower `x` | narrowing casts, arithmetic below `uint256` |
| inlined internal calls, library calls (`x.mulDivDown(y, d)`), named arguments | a call that writes storage inside a larger expression |
| `Math.min` and `Math.mulDiv`, backed by lemmas | other `Math` functions |
| storage writes with `=`, and events, when no part of them can revert (listed, not modeled) | a write that a later read sees, `+=` on storage |
| one of several overloads, by its parameter types | a variable or constant named `i`, a local that shadows another |

To get past a view or pure call that does not fit, name it in the read map: its result becomes an input.

## How it compares

solift is a small, inspectable workflow for one function's arithmetic, with a stated trust base. It does not claim
more power than these:

- **Halmos** runs symbolic tests on bytecode, in 256-bit arithmetic. solift uses it for the equivalence and the
  lemmas, and proves the property over unbounded integers.
- **hevm** also checks bytecode equivalence. solift adds the extraction and the integer theorems.
- **Certora** has `mathint`, summaries, and invariants across calls, in its own spec language. solift covers one call,
  with theorems in Python.
- **Kontrol** proves Foundry specifications in K, with lemmas and loop invariants.
- **SMTChecker** ships with the compiler and reasons across transactions and loops, with no link to the bytecode.

## Roadmap

Coverage: generated names that never clash with the source's, constant expressions, arithmetic below `uint256`, `%`,
and library summaries through the operator table. Then, as projects need them: `unchecked`, tuple returns, bounded
loops.

Past arithmetic:

1. **State transitions.** Storage writes become outputs of the model, named like reads (`$totalAssets` gives
   `q.post.totalAssets`), and the link compares them after the call. Theorems can then state conservation,
   monotonicity or exact deltas. The extractor already records each write and its place.
2. **Invariants over a set of functions**: for each function of the set, the invariant before the call and no revert
   give the invariant after it. The hard part is two reads of one mapping at keys that may be equal.
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

## License

Apache-2.0. See `LICENSE`.
