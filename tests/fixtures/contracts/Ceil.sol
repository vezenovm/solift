// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {Math as OlderMath} from "./utils/math/Math.sol";

// Math.ceilDiv of OpenZeppelin 5, which reverts where the divisor is zero, and of a copy written as 4.5 to 4.9 write
// it, which returns 0 for ceilDiv(0, 0).
contract Ceil {
    function half(uint256 _x) external pure returns (uint256) {
        return Math.ceilDiv(_x, 2);
    }

    function ceil(uint256 _a, uint256 _b) external pure returns (uint256) {
        return Math.ceilDiv(_a, _b);
    }

    function olderCeil(uint256 _a, uint256 _b) external pure returns (uint256) {
        return OlderMath.ceilDiv(_a, _b);
    }
}
