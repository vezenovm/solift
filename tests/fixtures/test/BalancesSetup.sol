// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Balances} from "../contracts/Balances.sol";

// The harness of the link of a function that reads the contract's own balance.
contract BalancesSetup is Test {
  Balances internal balances;

  function setUp() public virtual {
    balances = new Balances();
  }
}
