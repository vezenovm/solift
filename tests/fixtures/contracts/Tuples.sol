// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

// Functions of several returns, and the tuple declarations and assignments that take them apart, as in a pool's limits.
contract Tuples {
  uint256 internal $cap;

  function limits(uint256 _a, uint256 _b) public pure returns (uint256 sum, uint256 low) {
    sum = _a + _b;
    low = _a < _b ? _a : _b;
  }

  function pullable(uint256 _a, uint256 _b) public pure returns (uint256) {
    (, uint256 low) = limits(_a, _b);
    return low;
  }

  function split(uint256 _a) public view returns (uint256, bool) {
    if (_a > $cap) {
      return (_a - $cap, true);
    }
    (uint256 half, uint256 rest) = _halves(_a);
    (half, rest) = (rest, half);
    return (half - rest, false);
  }

  function _halves(uint256 _a) internal pure returns (uint256, uint256) {
    return (_a / 2, _a - _a / 2);
  }

  function forwarded(uint256 _a, uint256 _b) public pure returns (uint256, uint256) {
    return limits(_a, _b);
  }

  function partlyNamed(uint256 _a) public pure returns (uint256 first, uint256) {
    first = _a + 1;
  }

  function assignedTwice(uint256 _a) public pure returns (uint256 x) {
    (x, x) = (_a, 1);
  }

  function earlyReturn(uint256 _a) public pure returns (uint256 first, bool small) {
    first = _a;
    if (_a < 10) {
      return (first, true);
    }
    first = _a - 10;
  }
}
