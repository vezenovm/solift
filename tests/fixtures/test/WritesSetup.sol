// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Writes} from "../contracts/Writes.sol";

contract WritesHarness is Writes {
  constructor() Writes(IERC20(address(0))) {}

  function set(uint256 _stored, address _who, uint256 _balance) external {
    $info.rate = _stored;
    $balances[_who] = _balance;
  }
}

// The harness of the link of a function that writes storage before it returns.
contract WritesSetup is Test {
  WritesHarness internal writes;

  function setUp() public virtual {
    writes = new WritesHarness();
  }
}
