# solift

solift lifts a Solidity function to an integer model and proves theorems on it. For each function of a spec, it:

1. extracts an integer model from the solc AST. Each value that the function reads from outside itself (storage, a
   mapping entry, an external view call, `tx` and `block` fields, the fields of a struct parameter, or the result of an
   internal call that the spec cuts) becomes a named input, through a read map;
2. emits the model as pure Solidity, and proves with Halmos that this Solidity and the contract's bytecode give the same
   result and revert on the same inputs;
3. proves theorems on the model's z3 integer terms.

Nobody writes the formula. People write the theorems, the read map, and the test harness with its read adapters.

To use it on a new contract, see [USAGE.md](USAGE.md). [examples/vault](examples/vault) is a standalone Foundry
project with its specs, laid out as a project that uses solift lays out its own.

Status: two acceptance runs cover public contracts, each at a pinned commit, with harnesses in
`acceptance/<project>/harness/`:

- Resupply's lending pair, at the commit that the exploit of 2025-06-26 drained: the check that `borrow` makes,
  `_updateExchangeRate` and then `_isSolvent`. The theorem that a solvent borrower's debt is within twice the
  loan-to-value limit fails with the exploit as its counterexample, an oracle price above 1e36, and proves on the
  fixed source. This reproduces the arithmetic failure behind the exploit, not the transaction: that the solvency
  check reads the rate that `_updateExchangeRate` stored is a hypothesis of the harness, and the borrower's debt is a
  cut point.
- Morpho Blue v1.0.0: `_isHealthy`, with the same theorem, which proves, and a supply then a withdraw in its share math.
  Three of the theorems state rules of Morpho's Certora specs. `supplyWithdraw` is stated on a supply then a withdraw
  that the harness composes from SharesMathLib, not on `Morpho.supply` and `Morpho.withdraw`.
  `checkSummaryMulDivUp` and `checkSummaryMulDivDown` are theorems of the integer model of MathLib's `mulDivUp` and
  `mulDivDown` on any operands: the link proves those functions equal to the bytecode only where `_isHealthy` calls
  them.

The extractor takes loop-free functions with checked `uint` arithmetic, comparisons, boolean logic, ternaries, `if` and
early returns, `require`, `assert` and `revert`, locals assigned also in branches, compound assignments, named returns,
addresses, enums, constants and immutables, and `uint256(x)` of a narrower `x`. Internal calls are inlined, also a
library call on a value (`x.mulDivDown(y, d)`). `Math.min` and `Math.mulDiv` are operators backed by lemmas. Named
arguments bind by name. A target can name one of several functions of one name by its parameter types, and a parameter
of a user-defined value type, such as Morpho's `Id`, that only read map keys use is not an input. A function can write
storage and emit events: the model is its value and its revert, it lists the writes and events it leaves out, and the
extractor refuses a write that could change either (`extract.py`). It refuses loops, `unchecked`, signed integers,
narrowing casts, tuple returns, exponentiation, `%`, assembly, a local that shadows another variable, a variable or
constant named `i` (the inputs struct of the emitted Solidity), a constant with the name of a parameter, local or
return, and a function without inputs, with the place in the source. The type of each operation must match the type
in solc's AST.

## Run

Needs Python 3.12 or later, and forge. `SOLIFT_FORGE` names the forge binary (default `forge`), for a toolchain that
ships it under another name.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e . -r requirements-dev.txt
git submodule update --init
.venv/bin/solift reads tests/fixtures/contracts/Quoter.sol:Quoter._quote   # the read map that a function needs
.venv/bin/solift run examples.quoter.spec                # one spec on the test fixtures
.venv/bin/python -m examples.pricer                       # three specs in one run
(cd examples/vault && ../../.venv/bin/solift run vault_specs.spec --root .)   # a standalone project
.venv/bin/python -m acceptance.resupply --root <resupply>    # setup in acceptance/resupply/__main__.py
.venv/bin/python -m acceptance.morpho --root <morpho-blue>   # setup in acceptance/morpho/__main__.py
```

A spec (`examples/quoter/spec.py`) names the target function, its read map, its link to the harness, and its theorems.
A run takes one spec or several specs of one project. It builds the project once, extracts each model, and then runs
every stage, even when one fails: the theorems, the vectors (one forge test for every model), and the links (one Halmos
run for every model, with one test of the operator rows and lemmas they use). It prints one line for each result, and
writes a JSON record (`schema` 3) with each result, the sources each model comes from with their hashes, and the
versions of solc, OpenZeppelin, Halmos, yices, z3, cvc5, forge, Python and solift (with its commit). It also holds the
sha256 of each solc input of the build, of each theorem's query, of the spec's Python files and of each file that the
overlay adds, the profile, and the options of the link engine. The run fails when any result is not the expected one.
An error in a stage, such as an exception in a theorem, fails that stage and the run goes on.

Code on top of the models, such as a check of each deployed configuration, uses `solift.load(specs, workspace)`: the
terms of each model from one build. `q.at(name=value, ...)` gives the terms with inputs fixed, `q.call(function, *args)`
runs a function of the model, and `q.declared(local)` is a local's value where it is declared.

To pin solift from another project, install it by commit: `pip install git+https://github.com/vezenovm/solift@<commit>`.

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
check, when Halmos exits with a failure, and when Halmos skips a contract. A theorem runs on z3 first. When z3 says
"unknown", z3, yices and cvc5 run in parallel on the same query, and the first sat or unsat answer counts. "Unknown"
from every solver fails the theorem.

A cut point, such as `_price()`, is an input of the model. The link sets it through the harness, such as a
price feed with a symbolic answer, so it proves the model equal to the contract only for the values the harness can
produce.

## What it trusts

The integer translation in `smt.py` and the operator table (the vectors check them on samples), Halmos's EVM model, the
solvers, the harness and its read adapters, and the compiler. A read of an external view call, and a cut point, is
assumed to succeed.

## How it builds a project without writing to it

The project that holds the target is read-only. A run builds an overlay root in its workspace (`build/<spec>`): symlinks
to the project's `foundry.toml` and dependencies, copies of the sources that forge compiles as project files (forge
finds no AST for a symlinked project file), local files at paths that the project does not have (`add`), such as the
harness of a contract that the project does not test, and the generated files. Forge and Halmos run with `FOUNDRY_OUT`
and `FOUNDRY_CACHE_PATH` in the workspace: Halmos runs `forge build` without `--out` and reads the build from
`--forge-build-out`, so these variables are what keep the build out of the project. Each run builds once, from empty
directories, with Halmos's own build flags, and the vectors and Halmos extend that build: every artifact has its AST,
and a skipped contract still fails the run. Each write into the overlay refuses a link and a path outside it. A process
locks each workspace it uses, so two runs never share one, and a run does not delete a workspace that another process
holds. A guard compares the project's git status and file times before and after the run, and fails the run on a
change. It detects a write that changes the status or a file time; it does not prove that nothing was written.

## Develop

```sh
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
```

`tests/test_boundary.py` fails when the package imports anything other than the standard library, itself and its
pinned dependencies, and when a Python file names a path in a home directory.

## License

Apache-2.0. See `LICENSE`.
