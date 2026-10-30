# A standalone example

A Foundry project with a vault's share math (`src/Vault.sol`), and its specs in `vault_specs/`, the way a project that
uses solift keeps them: the harness next to the specs, the project untouched. CI copies this directory out of the
repo, installs solift from its wheel, and runs it there.

```sh
cp -rL examples/vault /tmp/vault && cd /tmp/vault    # -L copies forge-std and OpenZeppelin out of the links in lib
solift reads src/Vault.sol:Vault.previewRedeem       # the read map that previewRedeem needs
solift run vault_specs.spec --root .                 # both specs: the theorems, the vectors and the Halmos link
```

`redeem-pays-at-most-the-assets` proves without `assume=("inside",)`: its hypotheses keep the mulDiv operands below
2**128, so the record says `lemma_domain: established`. `redeem-pays-the-exact-part` is an expected counterexample:
the redeem rounds down.
