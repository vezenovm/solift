// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

// require, assert and revert.
contract Reverts {
  error Zero(uint256 value);

  function checked(uint256 _x) internal pure returns (uint256) {
    require(_x > 0, "zero");
    if (_x == 1) {
      revert Zero(_x);
    }
    require(_x != 2, Zero(_x));
    assert(_x != 3);
    return _x;
  }

  function alwaysReverts(uint256 _x) internal pure returns (uint256) {
    revert("no");
  }
}
