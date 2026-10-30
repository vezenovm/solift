// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Pointers} from "../contracts/Pointers.sol";

contract PointersHarness is Pointers {
  function set(address _who, uint64 _activeUntil, uint128 _perPeriod, uint192 _left, uint64 _last) external {
    $spenders[_who] = Spender({config: Config({activeUntil: _activeUntil, perPeriod: _perPeriod}), left: _left, last: _last});
  }

  function setMain(uint192 _left) external {
    $main.left = _left;
  }
}

// The harness of the link of a function that reads a struct in a mapping through storage pointers.
contract PointersSetup is Test {
  PointersHarness internal pointers;

  function setUp() public virtual {
    pointers = new PointersHarness();
  }
}
