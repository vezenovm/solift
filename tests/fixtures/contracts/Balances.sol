// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

interface IPayer {
  function pay() external;

  function approve(IERC20 _token, uint256 _amount) external;
}

// The balance of an address as a read, the bounds of a type as constants, and a conversion to an interface in the
// argument of a call, as in a pool that pays out of its ether.
contract Balances {
  uint256 internal constant ALL = type(uint256).max;

  function pot() public view returns (uint256) {
    return address(this).balance;
  }

  function share(uint256 _amount) external view returns (uint256) {
    uint256 amount = _amount == ALL ? pot() : _amount;
    return amount <= pot() ? amount : type(uint128).max;
  }

  function balanceOf(address _who) external view returns (uint256) {
    return _who.balance + type(uint64).min;
  }

  function balancesOfTwo(address _a, address _b) external view returns (uint256) {
    address who = _a;
    uint256 first = who.balance;
    who = _b;
    return first + who.balance;
  }

  function balanceAfterACall(IPayer _payer) external returns (uint256) {
    _payer.pay();
    return address(this).balance;
  }

  function potAfterACall(IPayer _payer) external returns (uint256) {
    uint256 before = pot();
    _payer.pay();
    return before + pot();
  }

  function approveAll(IPayer _payer, address _token) external returns (uint256) {
    _payer.approve(IERC20(_token), type(uint256).max);
    return 1;
  }
}
