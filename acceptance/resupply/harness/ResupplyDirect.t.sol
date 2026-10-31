// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {ResupplySetup} from "_/ResupplySetup.sol";

// Exact arithmetic past 2**256: a number below 2**512 is a pair of words, hi * 2**256 + lo. Each function is free of
// branches, so that it adds no paths to a symbolic run, and uses only 256-bit MUL, ADD, shifts and comparisons: a
// product of 128-bit halves never wraps.
library Wide {
    uint256 internal constant HALF = 0xffffffffffffffffffffffffffffffff;

    /// a * b, exactly.
    function mul(uint256 a, uint256 b) internal pure returns (uint256 hi, uint256 lo) {
        assembly {
            let a1 := shr(128, a)
            let a0 := and(a, HALF)
            let b1 := shr(128, b)
            let b0 := and(b, HALF)
            let low := mul(a0, b0)
            // a0 * b1 + (low >> 128) is below 2**256; adding a1 * b0 can carry once
            let middle := add(mul(a0, b1), shr(128, low))
            let sum := add(middle, mul(a1, b0))
            lo := or(shl(128, sum), and(low, HALF))
            hi := add(add(mul(a1, b1), shr(128, sum)), shl(128, lt(sum, middle)))
        }
    }

    /// (hi * 2**256 + lo) * b, and whether it is 2**512 or more, when the words are the low 512 bits.
    function mul(uint256 hi, uint256 lo, uint256 b) internal pure returns (bool over, uint256 rhi, uint256 rlo) {
        (uint256 h0, uint256 l0) = mul(lo, b);
        (uint256 h1, uint256 l1) = mul(hi, b);
        assembly {
            rhi := add(h0, l1)
            over := or(iszero(iszero(h1)), lt(rhi, h0))
        }
        rlo = l0;
    }

    /// x + y, and whether it is 2**512 or more, when the words are the low 512 bits.
    function add(uint256 xhi, uint256 xlo, uint256 yhi, uint256 ylo)
        internal
        pure
        returns (bool over, uint256 hi, uint256 lo)
    {
        assembly {
            lo := add(xlo, ylo)
            let s := add(xhi, yhi)
            hi := add(s, lt(lo, xlo))
            over := or(lt(s, xhi), lt(hi, s))
        }
    }

    /// x < y.
    function lt(uint256 xhi, uint256 xlo, uint256 yhi, uint256 ylo) internal pure returns (bool r) {
        assembly {
            r := or(lt(xhi, yhi), and(eq(xhi, yhi), lt(xlo, ylo)))
        }
    }
}

library Bound {
    uint256 internal constant LTV_PRECISION = 1e5;
    uint256 internal constant EXCHANGE_PRECISION = 1e18;

    /// borrowed * LTV_PRECISION * EXCHANGE_PRECISION < 2 * (maxLTV + 1) * collateral * price, over exact integers. The
    /// debt side is below 2**333, so a value side of 2**512 or more is larger.
    function withinTwiceTheLtv(uint256 borrowed, uint256 maxLTV, uint256 collateral, uint256 price)
        internal
        pure
        returns (bool r)
    {
        (uint256 debtHi, uint256 debtLo) = Wide.mul(borrowed, LTV_PRECISION * EXCHANGE_PRECISION);
        (uint256 hi, uint256 lo) = Wide.mul(collateral, price);
        (bool over1, uint256 h1, uint256 l1) = Wide.mul(hi, lo, maxLTV);
        (bool over2, uint256 h2, uint256 l2) = Wide.add(h1, l1, hi, lo);
        (bool over3, uint256 h3, uint256 l3) = Wide.add(h2, l2, h2, l2);
        bool below = Wide.lt(debtHi, debtLo, h3, l3);
        assembly {
            r := or(or(or(over1, over2), over3), below)
        }
    }
}

// The theorems of acceptance/resupply/solvency.py, stated on the bytecode, without the model: the same harness,
// inputs and adapters as the link that solift generates, and the theorem's hypotheses and conclusion. The run
// (direct.py) runs one check with Halmos, and replays each counterexample with forge.
contract ResupplyDirect is ResupplySetup {
    /// solvent-debt-within-twice-the-ltv: a borrower that the pair finds solvent has debt below twice
    /// (maxLTV + 1) / LTV_PRECISION of the collateral's value at the oracle price.
    function check_solvent_debt_within_twice_the_ltv(
        address _borrower,
        uint256 price,
        uint256 storedRate,
        uint256 maxLTV,
        uint256 collateral,
        uint128 totalAmount,
        uint128 totalShares,
        uint256 shares
    ) public {
        (bool hypotheses, bool bound) = outcome(
            _borrower, price, storedRate, maxLTV, collateral, totalAmount, totalShares, shares, type(uint256).max
        );
        vm.assume(hypotheses);
        assert(bound);
    }

    /// solvent-debt-within-twice-the-ltv-up-to-a-price-of-1e36: the same, up to an oracle price of 1e36.
    function check_solvent_debt_within_twice_the_ltv_up_to_a_price_of_1e36(
        address _borrower,
        uint256 price,
        uint256 storedRate,
        uint256 maxLTV,
        uint256 collateral,
        uint128 totalAmount,
        uint128 totalShares,
        uint256 shares
    ) public {
        (bool hypotheses, bool bound) =
            outcome(_borrower, price, storedRate, maxLTV, collateral, totalAmount, totalShares, shares, 1e36);
        vm.assume(hypotheses);
        assert(bound);
    }

    /// Whether the theorem's hypotheses hold, and whether its bound holds. The hypotheses: the pair has a
    /// loan-to-value limit and the borrower has debt (`lent`), the price is at most `cap`, the call does not revert,
    /// and its result is solvent. The inputs are set as the link sets them, and `borrowed` is read as it reads it.
    function outcome(
        address _borrower,
        uint256 price,
        uint256 storedRate,
        uint256 maxLTV,
        uint256 collateral,
        uint128 totalAmount,
        uint128 totalShares,
        uint256 shares,
        uint256 cap
    ) internal returns (bool hypotheses, bool bound) {
        stub.setPrice(price);
        pair.set(_borrower, maxLTV, storedRate, collateral, totalAmount, totalShares, shares);
        uint256 borrowed = pair.borrowerAmount(_borrower);
        if (maxLTV == 0 || borrowed == 0 || price > cap) return (false, false);
        (bool ok, bytes memory out) = address(pair).call(abi.encodeCall(pair.solventAfterUpdate, (_borrower)));
        if (!ok || !abi.decode(out, (bool))) return (false, false);
        return (true, Bound.withinTwiceTheLtv(borrowed, maxLTV, collateral, price));
    }
}

// The helpers on concrete values, against Python's integers. The run runs these with forge before Halmos.
contract ResupplyDirectWide is Test {
    uint256 internal constant MAX = type(uint256).max;

    function test_mul() public {
        _mul(0, MAX, 0, 0);
        _mul(1 << 255, 4, 2, 0);
        _mul(MAX, MAX, MAX - 1, 1);
        _mul(1 << 128, 1 << 128, 1, 0);
        _mul((1 << 128) - 1, (1 << 128) + 1, 0, MAX);
        _mul(
            0xdeadbeef00000000000000000000000000000000000000000000003039,
            0x3fbb7eb3800000000000000000000000000000000000000000000309,
            0x376fd91cf06202948000000000000000000000000000000000,
            0x2afdeb10444f7800000000000000000000000000000000000000000925d01
        );
    }

    function test_mul_and_add_say_when_they_pass_2_512() public {
        (bool over, uint256 hi, uint256 lo) = Wide.mul(1, 0, 2);
        assertTrue(!over && hi == 2 && lo == 0);
        (over,,) = Wide.mul(1 << 255, 0, 2);
        assertTrue(over);
        (over,,) = Wide.mul(MAX, MAX, MAX);
        assertTrue(over);
        (over, hi, lo) = Wide.mul(0, MAX, MAX);
        assertTrue(!over && hi == MAX - 1 && lo == 1);
        (over, hi, lo) = Wide.add(0, MAX, 0, 1);
        assertTrue(!over && hi == 1 && lo == 0);
        (over, hi, lo) = Wide.add(MAX, MAX, 0, 1);
        assertTrue(over && hi == 0 && lo == 0);
        (over,,) = Wide.add(MAX, 0, 1, 0);
        assertTrue(over);
        (over, hi, lo) = Wide.add(MAX - 1, MAX, 0, 1);
        assertTrue(!over && hi == MAX && lo == 0);
    }

    function test_lt() public {
        assertTrue(Wide.lt(0, MAX, 1, 0));
        assertFalse(Wide.lt(1, 0, 0, MAX));
        assertTrue(Wide.lt(5, 1, 5, 2));
        assertFalse(Wide.lt(5, 2, 5, 2));
    }

    function test_within_twice_the_ltv() public {
        // solift's counterexample, and the readme's
        _bound(80000000000001, 1, 2, 1000000000000000000000000000000000002, false);
        _bound(240000000000004, 3, 2, 1500000000000024999999999999999999488, false);
        // the two sides equal, and the debt one below
        _bound(4, 1, 1e23, 1, false);
        _bound(3, 1, 1e23, 1, true);
        _bound(MAX, (1 << 22) - 1, MAX, 5 ** 23, false);
        _bound(MAX - 1, (1 << 22) - 1, MAX, 5 ** 23, true);
        // the debt side past 2**256, and the value side below it, past it, and past 2**512 at each step
        _bound(MAX, 1, 1 << 200, 1 << 100, false);
        _bound(MAX, 1, MAX, 1 << 100, true);
        _bound(MAX, MAX, 1, 1, false);
        _bound(1 << 255, MAX, 1 << 255, 1 << 255, true);
        _bound(MAX, 1, MAX, MAX, true);
        _bound(MAX, 1, 1 << 255, 1 << 255, true);
        _bound(MAX, 0, MAX, MAX, true);
        _bound(MAX, MAX, MAX, MAX, true);
    }

    function _mul(uint256 a, uint256 b, uint256 hi, uint256 lo) internal {
        (uint256 h, uint256 l) = Wide.mul(a, b);
        assertEq(h, hi);
        assertEq(l, lo);
        (h, l) = Wide.mul(b, a);
        assertEq(h, hi);
        assertEq(l, lo);
    }

    function _bound(uint256 borrowed, uint256 maxLTV, uint256 collateral, uint256 price, bool expected) internal {
        assertEq(Bound.withinTwiceTheLtv(borrowed, maxLTV, collateral, price), expected);
    }
}
