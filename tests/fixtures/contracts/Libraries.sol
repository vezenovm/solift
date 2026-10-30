// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

type Key is bytes32;

// The shape of Morpho Blue's MathLib and SharesMathLib: internal library functions called on a value.
library Shares {
  using Shares for uint256;

  uint256 internal constant VIRTUAL_SHARES = 1e6;

  function mulDivDown(uint256 x, uint256 y, uint256 d) internal pure returns (uint256) {
    return (x * y) / d;
  }

  function toSharesDown(uint256 assets, uint256 totalAssets, uint256 totalShares) internal pure returns (uint256) {
    return assets.mulDivDown(totalShares + VIRTUAL_SHARES, totalAssets + 1);
  }
}

// The shape of Morpho Blue's _isHealthy: a position in a mapping at a user-defined key, with uint128 fields widened to
// uint256, and two functions of one name.
contract Libraries {
  using Shares for uint256;

  struct Position {
    uint128 shares;
    uint128 collateral;
  }

  mapping(Key => mapping(address => Position)) internal $positions;

  function value(Key key, address who, uint256 price) internal view returns (uint256) {
    return uint256($positions[key][who].collateral).mulDivDown(price, 1e18);
  }

  function value(Key key, address who) internal view returns (uint256) {
    return value(key, who, 1e18);
  }

  function shares(uint256 assets, uint256 totalAssets, uint256 totalShares) internal pure returns (uint256) {
    return assets.toSharesDown(totalAssets, totalShares) + Shares.mulDivDown(assets, 1, 2);
  }

  function product(uint64 a, uint64 b) internal pure returns (uint256) {
    return uint256(a) * b;
  }
}
