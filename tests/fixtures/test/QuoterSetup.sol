// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {IToken, Quoter} from "../contracts/Quoter.sol";

contract TokenStub is IToken {
  uint256 public balance;

  function set(uint256 _balance) external {
    balance = _balance;
  }

  function balanceOf(address) external view returns (uint256) {
    return balance;
  }
}

contract QuoterHarness is Quoter {
  constructor(IToken _token) Quoter(_token) {}

  function exposedQuote(uint256 _gas, uint256 _price) external view returns (uint256) {
    return _quote(_gas, _price);
  }
}

// The harness of the Quoter link: it deploys the quoter with a token stub that returns the balance it is set to.
contract QuoterSetup is Test {
  TokenStub internal token;
  QuoterHarness internal quoter;

  function setUp() public virtual {
    token = new TokenStub();
    quoter = new QuoterHarness(token);
  }
}
