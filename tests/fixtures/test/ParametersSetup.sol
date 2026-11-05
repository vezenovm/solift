// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Parameters} from "../contracts/Parameters.sol";

contract ParametersHarness is Parameters {
  function setBonus(uint256 _bonus) external {
    $bonus = _bonus;
  }

  function last() external view returns (uint256) {
    return $last;
  }
}

// The harness of the links of functions that assign their parameters.
contract ParametersSetup is Test {
  ParametersHarness internal parameters;

  function setUp() public virtual {
    parameters = new ParametersHarness();
  }
}
