// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Math} from "./utils/math/Math.sol";
import {Packed} from "./utils/math/Packed.sol";

// Two values packed in one slot, read by a shift and a mask by constants; and the shifts and masks that the extractor
// refuses.
contract Bits {
    uint256 internal constant LOW = 0xffffffffffffffffffffffffffffffff;

    Packed internal packed;

    function set(uint256 _packed) external {
        packed = Packed.wrap(_packed);
    }

    function high() public view returns (uint256) {
        return Packed.unwrap(packed) >> 128;
    }

    function low() public view returns (uint256) {
        return Packed.unwrap(packed) & LOW;
    }

    function both() external view returns (uint256, uint256) {
        return (high(), low());
    }

    function maskFirst(uint256 _x) external pure returns (uint256) {
        return 0xff & _x;
    }

    function narrow(uint128 _x) external pure returns (uint128) {
        return _x >> 64;
    }

    function byParameter(uint256 _x, uint256 _k) external pure returns (uint256) {
        return _x >> _k;
    }

    function shiftedLeft(uint256 _x) external pure returns (uint256) {
        return _x << 1;
    }

    function notLowBits(uint256 _x) external pure returns (uint256) {
        return _x & 0xf0;
    }

    function everyBitOut(uint256 _x) external pure returns (uint256) {
        return _x >> 256;
    }

    function fullMask(uint256 _x) external pure returns (uint256) {
        return _x & type(uint256).max;
    }

    function addressWideMask(uint256 _x) external pure returns (uint256) {
        return _x & type(uint160).max;
    }

    function wrappedNarrow(uint8 _x) external pure returns (uint256) {
        return Packed.unwrap(Packed.wrap(_x)) >> 1;
    }

    function wrappedLiteral(uint8 _x) external pure returns (uint256) {
        return Packed.unwrap(Packed.wrap(1)) + _x;
    }

    uint256 internal constant VALUE = 0x1234;

    function constantMaskFirst(uint256 _x) external pure returns (uint256) {
        uint256 y = LOW & VALUE;
        return _x + y;
    }

    // Math.min on the user-defined value type: the lemma is of the overload on uint256.
    function valueTypeOverload(uint256 _x) external pure returns (uint256) {
        return Packed.unwrap(Math.min(Packed.wrap(_x), Packed.wrap(0)));
    }

    // Math.mulDiv with a rounding on the user-defined value type, which the overload check comes before.
    function valueTypeRounded(uint256 _x) external pure returns (uint256) {
        return Packed.unwrap(Math.mulDiv(Packed.wrap(_x), Packed.wrap(1), Packed.wrap(1), Math.Rounding.Floor));
    }

    function userOperator(uint256 _x) external view returns (uint256) {
        return Packed.unwrap(packed + Packed.wrap(_x));
    }
}
