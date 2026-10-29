// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";

// Checks with known Halmos outcomes, for the tests of the link engine's guards.
contract Guards is Test {
  function check_passes(uint256 _a) public pure {
    assert(_a / 2 <= _a);
  }

  function check_fails(uint256 _a) public pure {
    assert(_a != 7);
  }

  function check_stopsAtTheLoopBound(uint8 _n) public pure {
    uint256 sum = 0;
    for (uint256 k = 0; k < _n; k++) {
      sum += 1;
    }
    assert(sum <= 255);
  }
}
