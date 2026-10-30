// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.19;

import {Test} from "forge-std/Test.sol";
import {Morpho} from "src/Morpho.sol";
import {Id, MarketParams} from "src/interfaces/IMorpho.sol";
import {SharesMathLib} from "src/libraries/SharesMathLib.sol";

contract MorphoHarness is Morpho {
    constructor() Morpho(msg.sender) {}

    function exposedIsHealthy(MarketParams memory marketParams, Id id, address borrower, uint256 collateralPrice)
        external
        view
        returns (bool)
    {
        return _isHealthy(marketParams, id, borrower, collateralPrice);
    }

    function setPosition(
        Id id,
        address borrower,
        uint128 borrowShares,
        uint128 collateral,
        uint128 totalBorrowAssets,
        uint128 totalBorrowShares
    ) external {
        position[id][borrower].borrowShares = borrowShares;
        position[id][borrower].collateral = collateral;
        market[id].totalBorrowAssets = totalBorrowAssets;
        market[id].totalBorrowShares = totalBorrowShares;
    }
}

// What a supply of assets and a withdraw of the shares it minted compute, in one block with no interest accrued: supply
// mints assets.toSharesDown(totalSupplyAssets, totalSupplyShares) and adds the assets and the shares to the market's
// totals, and withdraw pays shares.toAssetsDown of the new totals.
contract MorphoShares {
    using SharesMathLib for uint256;

    function supplyThenWithdraw(uint256 assets, uint256 totalSupplyAssets, uint256 totalSupplyShares)
        external
        pure
        returns (uint256)
    {
        uint256 shares = assets.toSharesDown(totalSupplyAssets, totalSupplyShares);
        return shares.toAssetsDown(totalSupplyAssets + assets, totalSupplyShares + shares);
    }
}

contract MorphoSetup is Test {
    // The market's tokens, oracle and rate model, which _isHealthy does not read.
    address internal constant NONE = address(0);

    MorphoHarness internal morpho;
    MorphoShares internal shares;

    function setUp() public {
        morpho = new MorphoHarness();
        shares = new MorphoShares();
    }
}
