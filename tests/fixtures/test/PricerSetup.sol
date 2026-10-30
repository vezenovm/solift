// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {IToken} from "../contracts/Quoter.sol";
import {Kinds} from "../contracts/Kinds.sol";
import {Pricer} from "../contracts/Pricer.sol";

contract BalanceStub is IToken {
  mapping(address account => uint256 balance) public balances;

  function set(address _account, uint256 _balance) external {
    balances[_account] = _balance;
  }

  function balanceOf(address _account) external view returns (uint256) {
    return balances[_account];
  }
}

contract PricerHarness is Pricer {
  constructor(IToken _token) Pricer(_token) {}

  function exposedPriced(Kinds.Kind _kind, address _token, Meter memory _meter) external view returns (uint256) {
    return _priced(_kind, _token, _meter);
  }
}

// The harness of the Pricer links: it deploys the pricer with a token stub that keeps a balance for each account.
contract PricerSetup is Test {
  BalanceStub internal token;
  PricerHarness internal pricer;

  function setUp() public virtual {
    token = new BalanceStub();
    pricer = new PricerHarness(token);
  }
}
