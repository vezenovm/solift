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
integers, narrowing casts, `**`, `%` and assembly. A function can write storage when nothing after the
write reads what it changed. To cut out a call that does not fit, add its source text to the read map: its result
becomes an input. [What the extractor takes](#what-the-extractor-takes) has the full list.

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
it links `foundry.toml`, `remappings.txt`, the library directories and the top directory of each remapping's target
(`.deps` for `@lib/=.deps/lib/src/`), copies the source directory, and writes the generated files to `<test>/solift`.
Pass `link=`, `copy=`, `add=` or `generated=` to set one yourself; `solift reads` takes `--link` and `--copy`. For a
layout it cannot infer, such as a profile whose source directory only imports the contracts, build the `Project` by
hand (`acceptance/resupply/project.py`):

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

- `result` and `reverts`; for a function of several returns, `result` is a tuple (`q.result[1]`);
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
import: `uv tool install solift --with <package>`, or `pipx install solift` then `pipx inject solift <package>`. To
pin a commit: `pip install 'git+https://github.com/vezenovm/solift@<commit>'`. It runs on Linux and macOS.

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

## Reference

### What the extractor takes

| Takes | Refuses, with the place in the source |
|---|---|
| checked `uint` arithmetic, comparisons, boolean logic, ternaries | `unchecked`, signed integers, `**`, `%`, assembly |
| `if`, early returns, `require`, `assert`, `revert` | loops |
| locals, also assigned in branches; compound assignments; named returns | modifiers |
| several returns, tuple declarations and assignments to locals (`(, uint256 y) = f(a);`) | a tuple from an external call, a tuple assignment to storage |
| addresses, enums, constants, immutables, `uint256(x)` of a narrower `x` | narrowing casts, arithmetic below `uint256` |
| inlined internal calls, library calls (`x.mulDivDown(y, d)`), named arguments | a call that writes storage inside a larger expression |
| `Math.min` and `Math.mulDiv`, backed by lemmas | other `Math` functions |
| storage writes with `=`, and events, when no part of them can revert (listed, not modeled) | a write that a later read sees, `+=` on storage |
| one of several overloads, by its parameter types | a variable or constant named `i`, a local that shadows another |

### Read map keys

A key names a value that the function reads from outside itself, and maps it to an input name:

| Key | Reads |
|---|---|
| `$floor`, `TOKEN` | a storage variable or an immutable, by its name |
| `$config.cap` | a field of a storage struct, read directly or through a memory copy (`Config memory cfg = $config;`) |
| `$rates[_kind].cap` | a field of a struct in a mapping, through a memory copy, at a key that is a parameter |
| `$balances[_who]`, `position[id][borrower].collateral` | any other storage read, by its source text |
| `_meter.used` | a field of a memory struct parameter of the target |
| `tx.gasprice`, `block.timestamp`, `msg.sender` | a field of `tx`, `block` or `msg` |
| `TOKEN.balanceOf(address(this))` | an external view call, by its source text without whitespace |
| `_price()` | an internal call: a cut point, whose result becomes an input |

A key that the function does not read is an error, except a field of `tx`, `block` or `msg`, which every transaction
has. The model assumes that each read succeeds: an external call that reverts has no input in the model.
`solift reads` prints the keys that a function needs.

### Adapters

An adapter is the harness code that makes the contract read an input. Each input reaches the contract in exactly one
way: as an argument of `call`, through an adapter, or as an `observed` value.

| The function reads | The link |
|---|---|
| a storage variable or field | an adapter that calls a setter of the harness: `"supply": "vault.setSupply(supply);"` |
| several fields at once | one adapter for a tuple of inputs: `("margin", "cap"): "quoter.setConfig(margin, cap);"` |
| an external view call | an adapter that sets a stub: `"held": "asset.set(held);"` |
| `block.timestamp`, `block.number`, `block.basefee` | a cheatcode: `"time": "vm.warp(time);"`, `vm.roll`, `vm.fee` |
| `tx.gasprice` | `observed={"gasPrice": "tx.gasprice"}`: Halmos makes it symbolic, and no cheatcode sets it |
| a field of a struct parameter | an adapter that builds the argument: `"Pricer.Meter memory meter = Pricer.Meter(...);"` |
| a cut point | `parameters={"answer": "uint128"}` for the symbolic value that sets it, an adapter that uses it, and `observed` for the input that results: `"price": "vault.exposedPrice()"` |
| an immutable that the harness sets | `observed={"token": "address(pricer.TOKEN())"}`, which takes only that value |

`parameters` are symbolic parameters of the check that are not inputs: an adapter uses them to set what the contract
reads. `observed` maps an input to the expression that the check reads it from, before the call. `imports` adds the
import directives that the adapters need.

### Spec options

| Option | Meaning |
|---|---|
| `impossible` | Outcomes (`"pays"`, `"zero"`, `"reverts"`) that the spec asserts no input reaches. A vector that reaches one fails the run. An outcome that z3 proves no input reaches needs no entry: the record lists it as unreachable. |
| `stem` | The name of the generated contracts, for two targets whose contract and function names give the same names, such as `_quote` and `quote`. |

### The link

Each input is a symbolic parameter of the Halmos check, set in the contract through the call or an adapter, except an
`observed` input, which the check reads from the deployed contract before the call. A cut point is set through the
harness, such as a price feed with a symbolic answer, so the link proves the model equal to the contract only for the
values that the harness can produce.

A link check fails on anything but PASS, on a path stopped at the loop bound or at Halmos's `--depth` or `--width`
limit, on a missing or undeclared check, on a result from a file other than the generated one, on two results for one
check, when Halmos exits with a failure, and when Halmos skips a contract. The same build proves the operator rows and
the lemmas that the models use: `Math.min` is the smaller value, and `Math.mulDiv(x, y, d)` is `x * y / d` rounded down
when `x` and `y` are below 2**128 and `d` is not 0.

### How far a theorem reaches

- `lemma_domain` is `not-used` when no evaluation that the proof makes uses mulDiv, `established` when the hypotheses
  keep every such evaluation inside the domain of the lemma, with each argument in the range of its Solidity type,
  `not-established` when they do not, and `unknown` when the solvers cannot decide.
- `callees` names the functions that the theorem runs on its own arguments with `q.call`. The link covers them only
  where the target calls them, so a theorem on them is a theorem of the integer model.
- "Unknown" from every solver fails a theorem.

### The record

A run builds the project once. A failed theorem, vector or link check does not stop the others, but a failed build or
a failed write of the generated files ends the run, and a spec whose extraction fails gets no later checks.
`results.json` (`schema` 3) holds:

- `ok`, `failures`, and `times`, the seconds of each stage;
- `project`: the root, commit, dirty flag, profile, what the overlay links and copies, where the generated files go,
  and the sha256 of each added file;
- `versions` of the tools, `engine` with the options of the link, `build` with the sha256 of each solc input,
  `spec files` with the sha256 of each Python file of the specs, and `generated` with the sha256 of each generated file;
- `specs`, one for each spec:
  - `model`: its inputs, reads, callees, and the writes and events that it leaves out (`effects`);
  - `theorems`: each with its verdict, witness, the values of a counterexample or a witness, what it assumed,
    `lemma_domain`, `callees`, `link`, `query_sha256`, and the answer of each solver;
  - `vectors`: the count, how many agree, and which outcomes and boundaries were reached or unreachable;
  - `link`: each Halmos check with its verdict;
- `link`: the shared operator checks and the problems of the Halmos run, and `tracebacks` for an error that is not one
  of solift's own.
