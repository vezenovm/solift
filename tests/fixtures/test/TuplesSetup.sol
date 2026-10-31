// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Tuples} from "../contracts/Tuples.sol";

contract TuplesHarness is Tuples {
  function setCap(uint256 _cap) external {
    $cap = _cap;
  }
}

// The harness of the links of functions of several returns.
contract TuplesSetup is Test {
  TuplesHarness internal tuples;

  function setUp() public virtual {
    tuples = new TuplesHarness();
  }
}
