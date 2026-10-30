// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

interface IToken {
  function balanceOf(address _account) external view returns (uint256);
}

library Fees {
  uint128 internal constant TIP = 2 gwei;
}

// A quote for gas at a token price: a storage struct read through a memory copy, a storage scalar, an external view
// call, tx.gasprice and block.basefee, constants, an internal call with an early return, and the Math operators.
contract Quoter {
  struct Config {
    uint128 margin;
    uint128 cap;
    uint64 unused;
  }

  uint256 internal constant WAD = 1e18;

  IToken public immutable TOKEN;
  Config internal $config;
  uint256 internal $floor;

  constructor(IToken _token) {
    TOKEN = _token;
  }

  function setConfig(uint128 _margin, uint128 _cap, uint256 _floor) external {
    $config = Config({margin: _margin, cap: _cap, unused: 0});
    $floor = _floor;
  }

  function quote(uint256 _gas, uint256 _price) external view returns (uint256) {
    return _quote(_gas, _price);
  }

  function _quote(uint256 _gas, uint256 _price) internal view returns (uint256) {
    Config memory cfg = $config;
    if (_gas == 0) {
      return 0;
    }
    uint256 gasPrice = Math.min(tx.gasprice, block.basefee + Fees.TIP);
    uint256 cost = Math.mulDiv(_gas * gasPrice, _price, WAD);
    uint256 target = _withMargin(cost, cfg.margin);
    if (target <= $floor) return 0;
    return Math.min(Math.min(target - $floor, cfg.cap), TOKEN.balanceOf(address(this)));
  }

  function _withMargin(uint256 _cost, uint256 _margin) internal pure returns (uint256) {
    if (_margin == 0) {
      return _cost;
    }
    return _cost + _margin;
  }

  function _capDirectly() internal view returns (uint256) {
    return $config.cap;
  }
}
