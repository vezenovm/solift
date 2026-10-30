// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

interface IAsset {
  function balanceOf(address _account) external view returns (uint256);
}

// The share math of a vault, as in ERC-4626 with one virtual share and one virtual asset: a deposit mints
// assets * (supply + 1) / (held + 1) shares, and a redeem pays shares * (held + 1) / (supply + 1) assets, where held is
// the vault's balance of the asset. Both round down, in the vault's favor.
contract Vault {
  IAsset public immutable ASSET;
  uint256 public totalSupply;

  constructor(IAsset _asset) {
    ASSET = _asset;
  }

  function totalAssets() public view returns (uint256) {
    return ASSET.balanceOf(address(this));
  }

  function previewDeposit(uint256 _assets) public view returns (uint256) {
    return Math.mulDiv(_assets, totalSupply + 1, totalAssets() + 1);
  }

  function previewRedeem(uint256 _shares) public view returns (uint256) {
    return Math.mulDiv(_shares, totalAssets() + 1, totalSupply + 1);
  }
}
