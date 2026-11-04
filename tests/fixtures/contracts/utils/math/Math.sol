// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Packed} from "./Packed.sol";

// A Math library at the path of OpenZeppelin's, with overloads of min and mulDiv on a user-defined value type that
// compute something else: the extractor must not take them for the lemmas of the overloads on unsigned integers.
library Math {
    function min(uint256 _a, uint256 _b) internal pure returns (uint256) {
        return _a < _b ? _a : _b;
    }

    function min(Packed _a, Packed) internal pure returns (Packed) {
        return _a;
    }

    enum Rounding {
        Floor,
        Ceil
    }

    function mulDiv(Packed, Packed, Packed, Rounding) internal pure returns (Packed) {
        return Packed.wrap(0);
    }
}
