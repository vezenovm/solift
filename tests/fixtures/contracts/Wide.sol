// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

// A function with many inputs and two outputs, whose link check must fit the legacy code generator's stack.
contract Wide {
    uint256 internal a;
    uint256 internal b;
    uint256 internal c;
    uint256 internal d;
    uint256 internal e;
    mapping(address => uint256) internal held;

    function set(uint256 _a, uint256 _b, uint256 _c, uint256 _d, uint256 _e) external {
        (a, b, c, d, e) = (_a, _b, _c, _d, _e);
    }

    function setHeld(address _who, uint256 _held) external {
        held[_who] = _held;
    }

    function total() external view returns (uint256) {
        return a;
    }

    function heldOf(address _who) external view returns (uint256) {
        return held[_who];
    }

    function take(address _who, uint256 _x, uint256 _y) external returns (uint256) {
        uint256 amount = (_x * (b + c + d + e)) / (_y + 1);
        a = a - amount;
        held[_who] = held[_who] - amount;
        return amount;
    }
}
