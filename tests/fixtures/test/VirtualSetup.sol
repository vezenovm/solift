// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {IToken} from "../contracts/Quoter.sol";
import {DerivedFees, Shares, Tiers, TokenPool} from "../contracts/Virtual.sol";
import {TokenStub} from "./QuoterSetup.sol";

contract TokenPoolHarness is TokenPool {
  constructor(IToken _token) TokenPool(_token) {}

  function setSupply(uint256 _supply) external {
    supply = _supply;
  }
}

// The harness of the links of functions that call virtual functions.
contract VirtualSetup is Test {
  DerivedFees internal fees;
  Tiers internal tiers;
  Shares internal shares;
  TokenStub internal token;
  TokenPoolHarness internal pool;

  function setUp() public virtual {
    fees = new DerivedFees();
    tiers = new Tiers();
    shares = new Shares();
    token = new TokenStub();
    pool = new TokenPoolHarness(token);
  }
}
