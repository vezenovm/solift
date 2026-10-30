"""The fix of the exploit, resupplyfi/resupply d84b3bf631 (2025-07-07): _updateExchangeRate reverts on an oracle price
above 1e22. It is two replacements of the pinned sources, of the text that the fix commit changes. On the fixed source,
the theorem that found the exploit proves."""

from acceptance.resupply.solvency import solvent_debt_within_twice_the_ltv

CORE = "src/protocol/pair/ResupplyPairCore.sol"
CONSTANTS = "src/protocol/pair/ResupplyPairConstants.sol"
THEOREM = solvent_debt_within_twice_the_ltv.id

CHANGES = {
    CORE: (
        """        //convert price of collateral as debt is priced in terms of collateral amount (inverse)
        _exchangeRate = 1e36 / IOracle(_exchangeRateInfo.oracle).getPrices(address(collateral));
""",
        """        uint256 priceFromOracle = IOracle(_exchangeRateInfo.oracle).getPrices(address(collateral));
        //all prices should *normally* be within the 1e18 (or 1e15 for curvelend) range
        //reject any prices that are well beyond that
        if(priceFromOracle > 1e22){
            revert InvalidOraclePrice();
        }

        //convert price of collateral as debt is priced in terms of collateral amount (inverse)
        _exchangeRate = 1e36 / priceFromOracle;
""",
    ),
    CONSTANTS: (
        """    error OnlyProtocolOrOwner();
}""",
        """    error OnlyProtocolOrOwner();
    error InvalidOraclePrice();
}""",
    ),
}


def replacements(root) -> dict[str, str]:
    """The fixed sources, by path. Each changed text must occur exactly once."""
    fixed = {}
    for path, (old, new) in CHANGES.items():
        source = (root / path).read_text()
        if source.count(old) != 1:
            raise ValueError(f"the fix: {path} does not have the changed text exactly once")
        fixed[path] = source.replace(old, new)
    return fixed


def problems(record: dict) -> list[str]:
    """Why the run of the fixed source does not show that the fix closes the gap: the theorem must prove with a
    witness, so that its expected counterexample is the run's only failure, and the link must pass."""
    (spec,) = record["specs"]
    theorem = {t["id"]: t for t in spec.get("theorems", [])}.get(THEOREM, {})
    found = [
        f for f in record["failures"] if f != f"{spec['spec']}: theorem {THEOREM}: PROVED, expected COUNTEREXAMPLE"
    ]
    if theorem.get("verdict") != "PROVED" or theorem.get("witness") != "WITNESS":
        found.append(f"{THEOREM} is {theorem.get('verdict')} with witness {theorem.get('witness')}, not PROVED")
    if spec.get("link") is None:
        found.append("link: not run")
    return found
