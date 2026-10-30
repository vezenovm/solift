// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {IAsset, Vault} from "src/Vault.sol";

contract AssetStub is IAsset {
  uint256 internal held;

  function set(uint256 _held) external {
    held = _held;
  }

  function balanceOf(address) external view returns (uint256) {
    return held;
  }
}

contract VaultHarness is Vault {
  constructor(IAsset _asset) Vault(_asset) {}

  function setSupply(uint256 _supply) external {
    totalSupply = _supply;
  }
}

// The harness of the vault's links: it deploys the vault with an asset stub that returns the balance it is set to.
contract VaultSetup is Test {
  AssetStub internal asset;
  VaultHarness internal vault;

  function setUp() public {
    asset = new AssetStub();
    vault = new VaultHarness(asset);
  }
}
