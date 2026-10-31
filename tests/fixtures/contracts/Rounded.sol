// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

interface IPool {
  function exchange(int128 _i, int128 _j, uint256 _dx, uint256 _minDy) external;
}

// Math.max, Math.mulDiv with a rounding, and a parameter of a type that the model lacks, which only an external call
// that the model leaves out takes. Math.mulDiv rounded up has no lemma.
contract Rounded {
  IPool internal immutable POOL;

  constructor(IPool _pool) {
    POOL = _pool;
  }

  function share(uint256 _x, uint256 _y, uint256 _d) external pure returns (uint256) {
    return Math.max(Math.mulDiv(_x, _y, _d, Math.Rounding.Floor), Math.mulDiv(_y, _y, _d));
  }

  function swap(int128 _out, uint256 _dx) external returns (uint256) {
    uint256 minDy = Math.mulDiv(_dx, 99, 100, Math.Rounding.Floor);
    POOL.exchange(0, _out, _dx, minDy);
    return minDy;
  }

  function roundedUp(uint256 _x, uint256 _y, uint256 _d) external pure returns (uint256) {
    return Math.mulDiv(_x, _y, _d, Math.Rounding.Ceil);
  }

  function roundedBy(uint256 _x, Math.Rounding _rounding) external pure returns (uint256) {
    return Math.mulDiv(_x, 3, 2, _rounding);
  }

  function signedValue(int128 _out, uint256 _x) external pure returns (uint256) {
    return _out > 0 ? _x : 0;
  }
}
