// SPDX-License-Identifier: Apache-2.0
pragma solidity >=0.8.27;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@oz/token/ERC20/IERC20.sol";
import {IExecutor} from "@core/interfaces/IExecutor.sol";
import {MigrationSubsidy} from "@periphery/migration/MigrationSubsidy.sol";
import {PotToken, PortalStub, FeedStub} from "../src/SubsidyHarnesses.sol";

// Exposes the two internal functions that price a migration: _pricedGas and _quoteSubsidy.
contract MigrationSubsidyHarness is MigrationSubsidy {
  constructor(address _owner, IERC20 _token, address _sourcePortal, address _executor, FeedStub _feed)
    MigrationSubsidy(_owner, _token, _sourcePortal, address(0xDE57), _executor, bytes32(0), _feed)
  {}

  function exposedPricedGas(IExecutor.Flow _flow, Meter memory _meter) external view returns (uint256) {
    return _pricedGas(_flow, _meter);
  }

  function exposedQuote(uint256 _gas, uint256 _tokenPerEth) external view returns (uint256) {
    return _quoteSubsidy(_gas, _tokenPerEth);
  }
}

contract MigrationSubsidySetup is Test {
  address internal constant OWNER = address(0xA11CE);
  address internal constant EXECUTOR = address(0xE8EC);

  PotToken internal token;
  MigrationSubsidyHarness internal subsidy;

  function setUp() public virtual {
    token = new PotToken();
    PortalStub source = new PortalStub(IERC20(address(token)));
    subsidy = new MigrationSubsidyHarness(OWNER, IERC20(address(token)), address(source), EXECUTOR, new FeedStub());
  }

  function _configure(uint128 _targetRelayerProfit, uint128 _maxSubsidy) internal {
    vm.prank(OWNER);
    subsidy.setConfig(_targetRelayerProfit, _maxSubsidy, 0);
  }
}
