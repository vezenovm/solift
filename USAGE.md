# Using solift on a new contract

solift checks one Solidity function at a time. You write a harness, a spec and theorems. solift writes the model, its
Solidity and the Halmos link test. Your project is read-only: a run builds in `build/<name>`.

To start, copy `examples/vault/`, a standalone Foundry project with its specs, or `acceptance/morpho/`, which covers a
project outside this repo.

## 1. Check that the function fits, and find what it reads

```sh
solift reads 'src/Vault.sol:Vault._redeem' --root <project> [--profile deploy]
```

`solift reads` builds the project into `build/reads` and runs the extractor until it has every read of the function:
storage variables and fields, mapping entries, fields of a struct parameter, fields of `tx`, `block` and `msg`, and
external calls. It prints them as a read map with proposed input names, a link skeleton, the internal calls that a read
map entry can cut, and the writes and events that the model leaves out. `--json` prints the same as JSON.

It stops at the first construct the extractor refuses, with its place in the source, and says "incomplete". The
function must be loop-free and use checked `uint` arithmetic: the extractor refuses loops, `unchecked`, signed
integers, narrowing casts, tuple returns, `**`, `%` and assembly. A function can write storage when nothing after the
write reads what it changed. To cut out a call that does not fit, add its source text to the read map: its result
becomes an input.

## 2. Write a harness

The harness is a Foundry test contract with `setUp()`. Keep it next to your spec, not in the project. It:

- deploys the contract, through a subclass with an `exposed` function when the target is internal;
- has setters for the state that the function reads, and stubs for the contracts that it calls.

The generated test inherits from the harness, so `call` and the adapters use its variables. A `Harness` puts it into
the overlay, at `<test>/solift/harness/` unless `at=` names a place. Its imports resolve from there, so import the
project's files by their path from the root (`import {Vault} from "src/Vault.sol";`), not by a relative path.

## 3. Write the spec

```python
import pathlib

from solift import Harness, Link, Project, Spec

HERE = pathlib.Path(__file__).parent
PROJECT = Project.foundry(profile=None)  # the profile the contract deploys with; None is the default
SPEC = Spec(
    name="vault-redeem",
    target="src/Vault.sol:Vault._redeem",  # add "(uint256,address)" to pick one overload
    reads={  # a value the function reads -> its input name, as `solift reads` prints them
        "totalAssets": "assets",  # storage, by name
        "$fees.rate": "rate",  # a field of a storage struct
        "ORACLE.price()": "price",  # an external view call, by its source text
        "block.timestamp": "time",
    },
    link=Link(
        setup=Harness(HERE / "VaultSetup.sol", "VaultSetup"),
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

`Project.foundry()` reads the project's foundry config (`forge config --json`, under the profile) when the run starts:
it links `foundry.toml`, `remappings.txt` and the library directories, copies the source directory, and writes the
generated files to `<test>/solift`. Pass `link=`, `copy=`, `add=` or `generated=` to set one yourself. For a layout it
cannot infer, such as a profile whose source directory only imports the contracts, build the `Project` by hand
(`acceptance/resupply/project.py`):

```python
PROJECT = Project(
    profile="deploy",
    link=("foundry.toml", "remappings.txt", "node_modules"),  # symlinked into the overlay
    copy=("src",),  # copied: forge finds no AST for a symlinked source
    add={"_/VaultSetup.sol": HERE / "VaultSetup.sol"},  # local files at paths the project does not have
    generated="_/solift",  # inside the profile's test directory
)
```

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
- `free(name, kind)`, a variable that the theorem quantifies over: an unbounded integer by default, or `"bool"`,
  `"address"` or an unsigned type such as `"uint256"`, whose range the theorem then assumes;
- `call(function, *args)`, with `.value` and `.reverts`;
- `at(**inputs)`, which fixes inputs.

Options:

| Option | Meaning |
|---|---|
| `assume` | `"no-revert"`: the model does not revert. `"inside"`: no `mulDiv` operand reaches 2**128. |
| `given` | More hypotheses, as a function of `q`. |
| `witness` | Required to prove. The hypotheses and the witness must hold together, or the theorem fails as VACUOUS. |
| `expect="counterexample"` | Pins a known failure, with `reason=` or `issue=`. |
| `show` | Locals to print with a counterexample, next to every input and free variable. |

## 5. Run

```sh
solift run myproject.spec --root <project> --no-link    # extraction, theorems and vectors, without Halmos
solift run myproject.spec --root <project>              # also the Halmos link
solift run myproject.spec:SPECS --root <project>        # a list of specs of one project, in one run
solift run myproject.spec --root <project> --emit out/  # also writes the model and the link test to out/
```

`solift run` imports the module from the current directory, without writing bytecode, and runs its `SPEC` or `SPECS`,
or the attribute after the colon. A module can also run itself: `sys.exit(solift.main(SPEC))`, then
`python -m myproject.spec` with the same options.

`SOLIFT_FORGE` names the forge binary. Each result prints one line, and the record is `build/<name>/results.json`. The
exit code is 0 only when every result is the expected one. Install solift as a tool, with the packages that your specs
import: `uv tool install solift --with <package>`, or `pipx install solift` then `pipx inject solift <package>`. It
runs on Linux and macOS.

## When it fails

- **`solift reads` says incomplete**, or **an extraction error** names the construct and its line. Cut the call in the read map, or expose a smaller function
  in the harness.
- **"the read map names X, which ... does not read"**: remove X from the read map.
- **The link fails**: an adapter does not set what the contract reads, or the read map misses a read.
- **The vectors disagree**: this is a solift bug.
- **COUNTEREXAMPLE**: the output prints every input and free variable. `show` adds locals.
- **A note under a theorem**: it rests on `Math.mulDiv` outside the domain this build proves (add `assume=("inside",)`
  or hypotheses that keep the operands below 2**128), or it is a theorem of the integer model of a callee.
- **"no input reaches it"** after the vectors: z3 proved that no input pays, returns zero or reverts. It is not a
  failure. `impossible=("zero",)` on the spec turns it into an assertion.
- **VACUOUS**: the hypotheses contradict each other, or the witness cannot hold with them.

The README says what a run proves and what it trusts.
