// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {ResupplyPairCore} from "src/protocol/pair/ResupplyPairCore.sol";
import {VaultAccount, VaultAccountingLibrary} from "src/libraries/VaultAccount.sol";

// One stub answers each call that the pair's constructor makes to its registry, collateral vault, underlying token,
// oracle and rate calculator. The link sets the oracle price.
contract ResupplyStub {
    uint256 public price = 1e18;

    function setPrice(uint256 _price) external {
        price = _price;
    }

    function token() external view returns (address) {
        return address(this);
    }

    function decimals() external pure returns (uint8) {
        return 18;
    }

    function asset() external view returns (address) {
        return address(this);
    }

    function approve(address, uint256) external pure returns (bool) {
        return true;
    }

    function convertToShares(uint256 _assets) external pure returns (uint256) {
        return _assets;
    }

    function getPrices(address) external view returns (uint256) {
        return price;
    }

    function getNewRate(address, uint256, uint256) external pure returns (uint64, uint128) {
        return (0, 0);
    }
}

contract ResupplyPairHarness is ResupplyPairCore {
    using VaultAccountingLibrary for VaultAccount;

    constructor(ResupplyStub _stub)
        ResupplyPairCore(
            address(_stub),
            abi.encode(address(_stub), address(_stub), address(_stub), uint256(0), uint256(0), uint256(0), uint256(0), uint256(0)),
            abi.encode(address(_stub)),
            abi.encode("solift", address(0), address(0), uint256(0))
        )
    {}

    // What borrow checks: borrow stores the rate that _updateExchangeRate returns, and its isSolvent modifier then
    // checks the borrower at the stored rate.
    function solventAfterUpdate(address _borrower) external returns (bool) {
        uint256 _rate = _updateExchangeRate();
        return _isSolvent(_borrower, _rate);
    }

    // The borrower's debt, as _isSolvent computes it.
    function borrowerAmount(address _borrower) external view returns (uint256) {
        return totalBorrow.toAmount(_userBorrowShares[_borrower], true);
    }

    function set(
        address _borrower,
        uint256 _maxLTV,
        uint256 _storedRate,
        uint256 _collateral,
        uint128 _totalAmount,
        uint128 _totalShares,
        uint256 _shares
    ) external {
        maxLTV = _maxLTV;
        exchangeRateInfo.exchangeRate = _storedRate;
        _userCollateralBalance[_borrower] = _collateral;
        totalBorrow = VaultAccount(_totalAmount, _totalShares);
        _userBorrowShares[_borrower] = _shares;
    }

    function totalCollateral() public pure override returns (uint256) {
        return 0;
    }

    function _stakeUnderlying(uint256) internal override {}

    function _unstakeUnderlying(uint256) internal override {}
}

contract ResupplySetup is Test {
    ResupplyStub internal stub;
    ResupplyPairHarness internal pair;

    function setUp() public {
        stub = new ResupplyStub();
        pair = new ResupplyPairHarness(stub);
    }
}
