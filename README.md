# z3sol

Solidity Arithmetic Prover (working name). For one Solidity function, it:

1. extracts an integer model from the solc AST. Each value that the function reads from outside itself (storage, an
   external view call, `tx` and `block` fields) becomes a named input, through a read map;
2. emits the model as pure Solidity, and proves with Halmos that this Solidity and the contract's bytecode give the same
   result and revert on the same inputs;
3. proves theorems on the model's z3 integer terms.

Nobody writes the formula. People write the theorems, the read map, and the test harness with its read adapters.

Status: milestone M1, which covers one function: `DepositSubsidy._quoteSubsidy` from Oxide.

## Run

Needs Python 3.12 or later, and forge. `Z3SOL_FORGE` names the forge binary (default `forge`; the aztec toolchain
ships it as `aztec-forge`).

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e . -r requirements-dev.txt
git submodule update --init
.venv/bin/python -m examples.quoter                     # the example on the test fixtures
Z3SOL_FORGE=aztec-forge .venv/bin/python -m acceptance.oxide --root <oxide>/l1-contracts
```

A spec (`examples/quoter/spec.py`) names the target function, its read map, its link to the harness, and its theorems.
A run extracts the model and then runs every stage, even when one fails: the theorems (z3), the vectors (forge), and
the link (Halmos). It prints one line for each result, and writes a record with each result, the sources the model
comes from with their hashes, and the versions of solc, OpenZeppelin, Halmos, yices, z3, forge and Python. The run fails
when any result is not the expected one.

## What a run proves

- The link: for every input, the contract and the emitted Solidity both succeed with the same result, or both revert.
  Each input is a parameter of the Halmos check, set in the contract through the call or a read adapter, except
  `tx.gasprice`, which Halmos already makes symbolic. Revert data, storage writes and events are not compared.
- The operator rows and lemmas that the model uses, proved in the same build: each checked operator reverts exactly
  when the table says, `Math.min` is the smaller value, and `Math.mulDiv` is the floor of the product when both
  operands are below 2**128.
- The theorems, each with its assumptions listed: the input types, and, when stated, that the model does not revert or
  is inside the lemma domain. A theorem expected to prove needs a witness that its hypotheses hold. An expected
  counterexample names its issue or reason.

A link check fails on anything but PASS, on a path at the loop bound, on a missing or undeclared check, and when Halmos
skips a contract. z3's "unknown" fails a theorem.

## What it trusts

The integer translation in `smt.py` and the operator table (the vectors check them on samples), Halmos's EVM model, the
solvers, the harness and its read adapters, and the compiler. A read of an external view call is assumed to succeed.

## How it builds a project without writing to it

The project that holds the target is read-only. A run builds an overlay root in its workspace (`build/<spec>`): symlinks
to the project's `foundry.toml` and dependencies, copies of the sources that forge compiles as project files (forge
finds no AST for a symlinked project file), and the generated files. Forge and Halmos run with `FOUNDRY_OUT` and
`FOUNDRY_CACHE_PATH` in the workspace: Halmos runs `forge build` without `--out` and reads the build from
`--forge-build-out`, so these variables are what keep the build out of the project. Each Halmos run starts from empty
directories. A guard compares the project's git status and file times before and after the run.

## Develop

```sh
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
```

`tests/test_no_oxide.py` fails when Python code here imports anything other than the standard library, this repo and
the pinned dependencies, edits `sys.path`, loads a module by path, or names a path outside the repo.

## License

Apache-2.0. See `LICENSE`.
