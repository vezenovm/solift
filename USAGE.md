# Using z3sol on a new contract

z3sol checks one Solidity function at a time. You write a harness, a spec and theorems. z3sol writes the model, its
Solidity and the Halmos link test. Your project is read-only: a run builds in `build/<name>`.

To start, copy `acceptance/morpho/`, which covers a project outside this repo, or `examples/quoter/`, which covers
contracts inside it.

## 1. Check that the function fits

The function must be loop-free and use checked `uint` arithmetic. The extractor refuses loops, `unchecked`, signed
integers, narrowing casts, tuple returns, `**`, `%` and assembly. A function can write storage when nothing after the
write reads what it changed. On anything else, the extractor stops with the construct and its place in the source. To
cut out a call that does not fit, add it to the read map: its result becomes an input.

## 2. Write a harness

The harness is a Foundry test contract with `setUp()`. Keep it next to your spec, not in the project. It:

- deploys the contract, through a subclass with an `exposed` function when the target is internal;
- has setters for the state that the function reads, and stubs for the contracts that it calls.

The generated test inherits from the harness, so `call` and the adapters use its variables.

## 3. Write the spec

```python
import pathlib
from z3sol.link import Link
from z3sol.run import Spec
from z3sol.workspace import Project

PROJECT = Project(
    profile=None,  # the Foundry profile the contract deploys with; None is the default
    link=("foundry.toml", "lib"),  # symlinked into the overlay
    copy=("src",),  # copied: forge finds no AST for a symlinked source
    add={"test/VaultSetup.sol": pathlib.Path(__file__).parent / "VaultSetup.sol"},  # the harness
    generated="test/z3sol",  # inside the profile's test directory
)
SPEC = Spec(
    name="vault-redeem",
    target="src/Vault.sol:Vault._redeem",  # add "(uint256,address)" to pick one overload
    reads={  # a value the function reads -> its input name
        "totalAssets": "assets",  # storage, by name
        "$fees.rate": "rate",  # a field of a storage struct
        "ORACLE.price()": "price",  # an external view call, by its source text
        "block.timestamp": "time",
    },
    link=Link(
        setup="test/VaultSetup.sol:VaultSetup",
        call="vault.exposedRedeem(shares, owner)",  # with the target's parameter names
        adapters={  # how the test sets each input
            ("assets", "rate"): "vault.set(assets, rate);",
            "price": "oracle.set(price);",
            "time": "vm.warp(time);",
        },
        observed={},  # an input that the test reads instead of setting, such as {"gasPrice": "tx.gasprice"}
    ),
    project=PROJECT,
)
```

Each input needs an adapter or an `observed` expression. Otherwise the link refuses to generate.

## 4. Write theorems

```python
@SPEC.theorem(assume=("no-revert",), witness=lambda q: q.result > 0)
def redeem_at_most_the_assets(q):
    """a redeem pays at most the vault's assets"""
    return q.result <= q.assets
```

The docstring is the statement. A theorem returns a z3 condition on `q`, which has:

- `result` and `reverts`;
- each input, parameter and local, by name;
- `free(name)`, a variable that the theorem quantifies over;
- `call(function, *args)`, with `.value` and `.reverts`;
- `at(**inputs)`, which fixes inputs.

Options:

| Option | Meaning |
|---|---|
| `assume` | `"no-revert"`: the model does not revert. `"inside"`: no `mulDiv` operand reaches 2**128. |
| `given` | More hypotheses, as a function of `q`. |
| `witness` | Required to prove. The hypotheses and the witness must hold together, or the theorem fails as VACUOUS. |
| `expect="counterexample"` | Pins a known failure, with `reason=` or `issue=`. |
| `show` | More names to print with a counterexample. |

## 5. Run

```python
# myproject/__main__.py
import sys
from myproject.spec import SPEC
from z3sol.cli import main

sys.exit(main(SPEC))  # or main([SPEC_A, SPEC_B]) for several functions of one project
```

```sh
python -m myproject --root <project> --no-link    # extraction, theorems and vectors, without Halmos
python -m myproject --root <project>              # also the Halmos link
python -m myproject --root <project> --emit out/  # also writes the model and the link test to out/
```

`Z3SOL_FORGE` names the forge binary. Each result prints one line, and the record is `build/<name>/results.json`. The
exit code is 0 only when every result is the expected one.

## When it fails

- **An extraction error** names the construct and its line. Cut the call in the read map, or expose a smaller function
  in the harness.
- **"the read map names X, which ... does not read"**: remove X from the read map.
- **The link fails**: an adapter does not set what the contract reads, or the read map misses a read.
- **The vectors disagree**: this is a z3sol bug.
- **COUNTEREXAMPLE**: the output prints the input values. `show` adds more.
- **VACUOUS**: the hypotheses contradict each other, or the witness cannot hold with them.

The README says what a run proves and what it trusts.
