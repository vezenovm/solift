# solift

solift proves theorems about Solidity functions. Support is limited today: see [What it accepts](#what-it-accepts)
and the [roadmap](#roadmap).

For each function of a spec, solift:

1. extracts a model of the function in solift IR from the source, with each outside read, such as storage or an
   external view call, as an input, and each write or call argument that you name as an output;
2. proves with Halmos that the model, emitted as Solidity, returns, reverts and sets its outputs as the bytecode does;
3. translates the model to SMT terms over unbounded integers, and proves your theorems, written in Python, on them
   with z3, then yices and cvc5, or prints a counterexample.

You write the theorems, a spec and a test harness. Nobody writes the model or a proof. When the function changes, the
next run extracts the model again, checks it against the bytecode and proves the theorems again. The spec and the
harness change only when the function's reads or its setup do.

![Halmos proves the Solidity that solift emits from the extracted model equal to the contract's bytecode, and the
solvers prove the theorems on the model's SMT terms, which sampled vectors check against the emitted
Solidity](docs/proof-chain.png)

## What it found

At the commit of Resupply's lending pair that the exploit of 2025-06-26 drained, the theorem "a borrower that the pair
finds solvent has debt below twice the loan-to-value limit of its collateral's value" fails:

```
COUNTEREXAMPLE WITNESS [solvent-debt-within-twice-the-ltv] a borrower that the pair finds solvent has debt below twice
  (maxLTV + 1) / LTV_PRECISION of the collateral's value at the oracle price [0.01s]
  _borrower=0 price=1000000000000000000000000000000000002 storedRate=0 maxLTV=1 collateral=2 borrowed=80000000000001
PROVED WITNESS [solvent-debt-within-twice-the-ltv-up-to-a-price-of-1e36] up to an oracle price of 1e36, ... [0.01s]
vectors: 192 of 192 agree
PASS ResupplyPairHarnessSolventAfterUpdateLink.check_link [0.76s]
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
  result and the same outputs, or both revert. Revert data, events, and the writes and calls that are not outputs are
  not compared.
- **The lemmas** that the model uses, in the same build: each checked operator reverts exactly when the table says,
  `Math.min` and `Math.max` are the smaller and the larger value, and `Math.mulDiv(x, y, d)` is `x * y / d` rounded
  down when `x` and `y` are below 2**128.
- **The theorems**, each with its assumptions listed, starting with the input types. A theorem that is expected to
  prove needs a witness, so that its hypotheses can hold, and an expected counterexample needs a reason.

Each theorem's record also says how far it reaches: `lemma_domain` (whether its hypotheses keep mulDiv where the lemma
is proved), `callees` (functions that it runs with `q.call` on its own arguments, which the link covers only where the
target calls them), and `link`. The run
writes a JSON record of every result, the tool versions, and the hashes of what each result rests on. It fails when a
result is not the expected one. [USAGE.md](USAGE.md#reference) has the details.

## What it trusts

The integer translation and the operator table (the vectors check them on samples), Halmos, the solvers, the compiler,
and your harness. External view calls and cut points are assumed not to revert, and an external call that the
function makes as a statement is assumed not to revert and not to call back into the contract.

## What it accepts

Loop-free functions in checked `uint` arithmetic, with branches, `require` and `revert`, inlined internal and library
calls, several returns, storage pointers to structs, conversions between unsigned widths, and `Math.min`, `Math.max` and
`Math.mulDiv`. A storage write or an external call argument can be an output of the model, which the link compares after
the call, so a function without a value fits too. The extractor refuses loops, `unchecked`, signed values, `**`, `%`,
assembly and modifiers, and names the place in the source. A view or pure call that does not fit can go in the read map:
its result becomes an input. [USAGE.md](USAGE.md#what-the-extractor-accepts) has the full list.

## Proof performance

The split takes a property's multiplication, division and rounding off the bytecode: the solvers prove the property
over exact integers, and Halmos only checks that two programs of the same shape agree. `Math.mulDiv` meets the
bytecode only in its lemma, once for each spec.

To measure what the split saves, the three Resupply checks also run directly on the bytecode, as Halmos tests with the
link's harness and inputs and the theorem's hypotheses
([ResupplyDirect.t.sol](acceptance/resupply/harness/ResupplyDirect.t.sol)). Halmos runs each with one solver for up to
an hour.

| Theorem | solift | Halmos with yices | Halmos with z3 |
| --- | --- | --- | --- |
| At the exploit: debt below twice the LTV limit | counterexample, 0.78s | counterexample, 1314s | no result in 1 hour |
| At the exploit: the same, up to a price of 1e36 | proved, 0.78s | no result in 1 hour | no result in 1 hour |
| On the fix: debt below twice the LTV limit | proved, 0.85s | no result in 1 hour | no result in 1 hour |

- "No result" means the run was stopped at the hour, not that the theorem cannot be proved on the bytecode.
- solift's time is the theorem query, the link and the lemmas. A whole run at the exploit, with the build and the
  test vectors, takes 14s.
- The direct check also runs `toAmount`, which solift cuts. Halmos's counterexample is the same bug, at an oracle
  price of about 4.6e46.
- Measured on an Apple M4 Pro (14 cores, 48 GiB, macOS 15.7.4), with solc 0.8.28, forge 1.8.5, Halmos 0.3.3,
  yices 2.6.4 and z3 4.12.6.

## How it compares

solift is a small, inspectable workflow for one call of a function, with a stated trust base. It does not claim
more power than these:

| Tool | Proves the property on | You write | Reach |
|---|---|---|---|
| **solift** | a model in solift IR, extracted from the source, which Halmos proves equal to the bytecode | theorems in Python, a read map and a harness | one loop-free call |
| Halmos | the bytecode, in 256-bit arithmetic | symbolic tests in Solidity | several calls in a test, loops up to a bound |
| hevm | the bytecode, in 256-bit arithmetic, and the equivalence of two bytecodes | symbolic tests, or a second program | loops up to a bound |
| Certora | the compiled code, with `mathint` in the spec | rules, invariants and summaries in CVL | invariants across calls |
| Kontrol | the bytecode, in K | Foundry tests, with lemmas and loop invariants | loops, with invariants |
| SMTChecker | the source, inside the compiler, with no link to the bytecode | assertions in the source | across transactions and loops |
| Lean on bytecode, such as [ml-dsa-fv](https://github.com/eth-act/ml-dsa-fv) | one fixed bytecode, in Lean | a proof for each bytecode | full specifications, gas included |

solift uses Halmos for the link and the lemmas. hevm could check the link too: solift adds the extraction and the
integer theorems.

A Lean proof on bytecode trusts only the Lean kernel and the EVM semantics, a smaller base than solift's. But the
proof follows the bytecode it is about, so a new contract or a changed one needs a new proof, even when an agent
writes it, and a proof that does not close does not say whether the property is false. In solift the theorems are
written once: each run extracts the model again, Halmos checks the link with no proof to write, and a false theorem
gives a counterexample.

## Roadmap

Coverage: generated names that never clash with the source's, constant expressions, arithmetic below `uint256`, `%`,
and library summaries through the operator table. Then, as projects need them: `unchecked`, modifiers, `**`,
`Math.mulDiv` rounded up (once Halmos can prove its `mulmod` at a symbolic denominator), bounded loops.

Past arithmetic:

1. **The rest of state transitions.** A write and an external call argument are outputs already (`$totalAssets` gives
   `q.post.totalAssets`), so a theorem can state an exact delta or conservation over one call. Still refused: an output
   written in an internal call, a read after a write at another key of the same storage variable, and an external
   call that only some paths make.
2. **Invariants over a set of functions**: for each function of the set, the invariant before the call and no revert
   give the invariant after it. The hard part is two reads of one mapping at keys that may be equal.
3. **External calls in the model.** An external call that the function makes as a statement is assumed not to revert
   and not to call back into the contract. In order: whether the call reverts, as an input, so that the link compares
   both outcomes; the arithmetic of a callee whose source is in the build, inlined, with its storage as reads and
   outputs at keys that name the contract; and callbacks, which need the invariants of item 2.
4. **Events**, as outputs that the link compares.

Trust: a one-time Lean proof that the integer terms of every model agree with its checked `uint256` execution, given
the rows of the operator table that Halmos proves. It covers how the rows compose, through branches, short circuits,
inlined calls and returns. What stays trusted is that `smt.py` follows the Lean definition, which the vectors keep
checking on samples.

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
# the Resupply checks directly on the bytecode, as in Proof performance: up to an hour each, so not in CI
.venv/bin/python -m acceptance.resupply.direct --root <resupply> --solver yices --budget 3600
```

## License

Apache-2.0. See `LICENSE`.
