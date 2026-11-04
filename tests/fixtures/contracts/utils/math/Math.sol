// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Packed} from "./Packed.sol";

// A Math library at the path of OpenZeppelin's, with an overload of min on a user-defined value type that computes
// something else: the extractor must not take it for the lemma of the overload on unsigned integers. Its ceilDiv is
// written as in OpenZeppelin 4.5 to 4.9, which return 0 for ceilDiv(0, 0).
library Math {
    function ceilDiv(uint256 a, uint256 b) internal pure returns (uint256) {
        return a == 0 ? 0 : (a - 1) / b + 1;
    }

    function min(uint256 _a, uint256 _b) internal pure returns (uint256) {
        return _a < _b ? _a : _b;
    }

    function min(Packed _a, Packed) internal pure returns (Packed) {
        return _a;
    }
}
