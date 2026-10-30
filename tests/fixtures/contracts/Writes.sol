// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

// Functions that write storage and emit events around the value they return, and the writes that the extractor refuses
// because the model, which leaves the writes out, would then differ from the function.
contract Writes {
  struct Info {
    address oracle;
    uint96 lastTimestamp;
    uint256 rate;
  }

  event Updated(uint256 rate);

  IERC20 public immutable TOKEN;
  Info internal $info;
  uint256 internal $count;
  mapping(address => uint256) internal $balances;
  uint256[] internal $list;

  constructor(IERC20 _token) {
    TOKEN = _token;
  }

  // The shape of Resupply's _updateExchangeRate.
  function update(uint256 _price) internal returns (uint256 rate) {
    Info memory info = $info;
    rate = 1e36 / _price;
    if (rate != info.rate) {
      info.lastTimestamp = uint96(block.timestamp);
      info.rate = rate;
      $info = info;
      emit Updated(rate);
    }
  }

  function updateThenRead(uint256 _price, address _who) external returns (uint256) {
    uint256 rate = update(_price);
    return rate + _balance(_who);
  }

  function _balance(address _who) internal view returns (uint256) {
    return $balances[_who];
  }

  function _rate() internal view returns (uint256) {
    return $info.rate;
  }

  function credit(address _who, uint256 _x) internal returns (uint256) {
    $balances[_who] = _x;
    return _x;
  }

  function readAfterWrite(uint256 _x) internal returns (uint256) {
    $count = _x;
    return $count;
  }

  function readCopyAfterWrite(uint256 _x) internal view returns (uint256) {
    Info memory info = $info;
    info.rate = _x;
    return info.rate;
  }

  function copyAfterWrite(uint256 _x) internal returns (uint256) {
    $info.rate = _x;
    Info memory info = $info;
    return info.rate;
  }

  function writeThatCanRevert(uint256 _x) internal returns (uint256) {
    $count = _x + 1;
    return _x;
  }

  function writeToAnArray(uint256 _x) internal returns (uint256) {
    $list[_x] = 1;
    return _x;
  }

  function eventThatCanRevert(uint256 _x) internal returns (uint256) {
    emit Updated(_x * 2);
    return _x;
  }

  function updateInsideAnExpression(uint256 _price) internal returns (uint256) {
    return update(_price) + 1;
  }

  function readsInfoAfterUpdate(uint256 _price) internal returns (uint256) {
    uint256 rate = update(_price);
    return rate + $info.rate;
  }

  function callsAReaderAfterUpdate(uint256 _price) internal returns (uint256) {
    uint256 before = _rate();
    uint256 rate = update(_price);
    return before + rate + _rate();
  }

  function externalReadAfterWrite(uint256 _x) internal returns (uint256) {
    $count = _x;
    return TOKEN.balanceOf(address(this));
  }

  function stateChangingRead() internal returns (uint256) {
    return TOKEN.transfer(address(1), 1) ? 1 : 0;
  }
}
