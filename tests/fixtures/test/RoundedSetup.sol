// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {IPool, Rounded} from "../contracts/Rounded.sol";

// A pool that records the arguments of the last exchange.
contract PoolStub is IPool {
  int128 public lastOut;
  uint256 public lastMinDy;

  function exchange(int128, int128 _j, uint256, uint256 _minDy) external {
    lastOut = _j;
    lastMinDy = _minDy;
  }
}

// The harness of the links of Math.max, Math.mulDiv with a rounding, and a swap with a signed index.
contract RoundedSetup is Test {
  PoolStub internal pool;
  Rounded internal rounded;

  function setUp() public virtual {
    pool = new PoolStub();
    rounded = new Rounded(pool);
  }
}
