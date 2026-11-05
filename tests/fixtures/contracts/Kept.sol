// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

// Calls in the arguments of a require's custom error, which solc evaluates whether or not the condition holds: taken
// where the condition makes the same call on every path, refused where it may not.
contract Kept {
    error NotKeeper(address caller, address keeper);
    error Bad(uint256 value);

    address internal keeper;

    function setKeeper(address _keeper) external {
        keeper = _keeper;
    }

    function currentKeeper() public view returns (address) {
        return keeper;
    }

    function kept(uint256 _a) external view returns (uint256) {
        require(msg.sender == currentKeeper(), NotKeeper(msg.sender, currentKeeper()));
        return _a;
    }

    function _inverse(uint256 _n) internal pure returns (uint256) {
        return 1 / _n;
    }

    function sameArgument(uint256 _n) external pure returns (uint256) {
        require(_inverse(_n) > 0, Bad(_inverse(_n)));
        return _n;
    }

    function skipped(bool _skip, uint256 _n) external pure returns (uint256) {
        require(_skip || _inverse(_n) > 0, Bad(_inverse(_n)));
        return _n;
    }

    function otherArgument(uint256 _n) external pure returns (uint256) {
        require(_inverse(1) > 0, Bad(_inverse(_n)));
        return _n;
    }
}
