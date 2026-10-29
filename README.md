# z3sol

Solidity Arithmetic Prover (working name). For one Solidity function, it:

1. extracts an integer model from the solc AST, with each read from outside the function (storage, an external view
   call, `tx` and `block` fields) as a named input;
2. emits the model as pure Solidity, and proves with Halmos that this Solidity and the contract's bytecode give the same
   result and revert on the same inputs;
3. proves hand-written theorems on the model's z3 terms.

Nobody writes the formula. People write the theorems, the read map and the test harness.

Status: milestone M1, which covers one function: `DepositSubsidy._quoteSubsidy` from Oxide.

## Develop

Needs Python 3.12 or later.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e . -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
```

## License

Apache-2.0. See `LICENSE`.
