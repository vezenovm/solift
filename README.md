# z3sol

Solidity Arithmetic Prover (working name). For each Solidity function of a spec, it:

1. extracts an integer model from the solc AST. Each value that the function reads from outside itself (storage, a
   mapping entry, an external view call, `tx` and `block` fields, the fields of a struct parameter, or the result of an
   internal call that the spec cuts) becomes a named input, through a read map;
2. emits the model as pure Solidity, and proves with Halmos that this Solidity and the contract's bytecode give the same
   result and revert on the same inputs;
3. proves theorems on the model's z3 integer terms.

Nobody writes the formula. People write the theorems, the read map, and the test harness with its read adapters.

Status: milestone M3. The acceptance run against Oxide covers the functions of Oxide's `formulas/subsidies.py`:
`DepositSubsidy._quoteSubsidy`, `DepositSubsidy.quoteSubsidy` (cut at `_tokenWeiPerEth()`), `DepositSubsidy._pricedGas`,
and `PlainWithdrawalSubsidy.quoteSubsidy` (cut at `_tokenWeiPerEth()`). It also covers two contracts that Oxide does not
prove, with their own theorems and with harnesses in `acceptance/oxide/harness/`: `Caps.getCurrentAvailable`, the
portal's deposit capacity, and `MigrationSubsidy._pricedGas` and `_quoteSubsidy`, which Oxide's registry exempts.
Wiring the tool into Oxide's proof check, and deleting `subsidies.py` there, happens from an Oxide session.

The extractor takes loop-free pure and view functions with checked `uint` arithmetic, comparisons, boolean logic,
ternaries, `if` and early returns, locals assigned also in branches, compound assignments, named returns, addresses,
enums, constants and immutables. Internal calls are inlined. `Math.min` and `Math.mulDiv` are operators backed by
lemmas. Named arguments bind by name. It refuses loops, storage writes, `unchecked`, signed integers, width casts, tuple
returns, exponentiation, `%`, `require`, assembly, a local that shadows another variable, a variable or constant named
`i` (the inputs struct of the emitted Solidity), a constant with the name of a parameter, local or return, and a
function without inputs, with the place in the source. The type of each operation must match the type in solc's AST.

## Run

Needs Python 3.12 or later, and forge. `Z3SOL_FORGE` names the forge binary (default `forge`; the aztec toolchain
ships it as `aztec-forge`).

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e . -r requirements-dev.txt
git submodule update --init
.venv/bin/python -m examples.quoter                     # one spec on the test fixtures
.venv/bin/python -m examples.pricer                     # three specs in one run
Z3SOL_FORGE=aztec-forge .venv/bin/python -m acceptance.oxide --root <oxide>/l1-contracts
```

A spec (`examples/quoter/spec.py`) names the target function, its read map, its link to the harness, and its theorems.
A run takes one spec or several specs of one project. It builds the project once, extracts each model, and then runs
every stage, even when one fails: the theorems, the vectors (one forge test for every model), and the links (one Halmos
run for every model, with one test of the operator rows and lemmas they use). It prints one line for each result, and
writes a JSON record (`schema` 2) with each result, the sources each model comes from with their hashes, and the
versions of solc, OpenZeppelin, Halmos, yices, z3, cvc5, forge, Python and z3sol (with its commit). The run fails when
any result is not the expected one.

Code on top of the models, such as a check of each deployed configuration, uses `z3sol.run.load(specs, workspace)`: the
terms of each model from one build. `q.at(name=value, ...)` gives the terms with inputs fixed, `q.call(function, *args)`
runs a function of the model, and `q.declared(local)` is a local's value where it is declared.

To pin z3sol from another project, install it by commit: `pip install git+https://github.com/vezenovm/z3sol@<commit>`.

## What a run proves

- The link: for every input, the contract and the emitted Solidity both succeed with the same result, or both revert.
  Each input is a parameter of the Halmos check, set in the contract through the call or a read adapter, except an
  `observed` input, which the check reads from the deployed contract: `tx.gasprice`, which Halmos already makes
  symbolic, a cut point, which takes the values that its own symbolic inputs give it, and an immutable that the
  harness sets, such as a token address, which takes only that value. Revert data, storage writes and events are not
  compared.
- The operator rows and lemmas that the model uses, proved in the same build: each checked operator reverts exactly
  when the table says, `Math.min` is the smaller value, and `Math.mulDiv` is the floor of the product when both
  operands are below 2**128.
- The theorems, each with its assumptions listed: the input types, and, when stated, that the model does not revert or
  is inside the lemma domain. A theorem expected to prove needs a witness that its hypotheses hold. An expected
  counterexample names its issue or reason.

A link check fails on anything but PASS, on a path stopped at the loop bound or at Halmos's `--depth` or `--width`
limit, on a missing or undeclared check, on a result from a file other than the generated one, on two results for one
check, when Halmos exits with a failure, and when Halmos skips a contract. A theorem runs on z3 first. When z3 says
"unknown", z3, yices and cvc5 run in parallel on the same query, and the first sat or unsat answer counts. "Unknown"
from every solver fails the theorem.

A cut point, such as `_tokenWeiPerEth()`, is an input of the model. The link sets it through the harness, such as a
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
holds. A guard compares the project's git status and file times before and after the run.

## Develop

```sh
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
```

`tests/test_no_oxide.py` fails when Python code here imports anything other than the standard library, this repo and
the pinned dependencies, edits `sys.path`, loads a module by path, or names a path outside the repo.

## License

Apache-2.0. See `LICENSE`.
