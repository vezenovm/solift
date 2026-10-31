// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Effects, ISwapper} from "../contracts/Effects.sol";

// A swapper that records the arguments of the last buy.
contract SwapperStub is ISwapper {
  uint256 public fee;
  address public lastRecipient;
  uint256 public lastAmount;

  function setFee(uint256 _fee) external {
    fee = _fee;
  }

  function buy(address _recipient, uint256 _amount) external {
    lastRecipient = _recipient;
    lastAmount = _amount;
  }
}

contract EffectsHarness is Effects {
  constructor(ISwapper _swapper) Effects(_swapper) {}

  function setLeft(address _who, uint192 _left) external {
    $allowances[_who].left = _left;
  }

  function leftOf(address _who) external view returns (uint192) {
    return $allowances[_who].left;
  }

  function lastOf(address _who) external view returns (uint64) {
    return $allowances[_who].last;
  }

  function setTotal(uint256 _total) external {
    $total = _total;
  }

  function total() external view returns (uint256) {
    return $total;
  }
}

// The harness of the links of functions whose writes and external call arguments are outputs.
contract EffectsSetup is Test {
  SwapperStub internal swapper;
  EffectsHarness internal effects;

  function setUp() public virtual {
    swapper = new SwapperStub();
    effects = new EffectsHarness(swapper);
  }
}
