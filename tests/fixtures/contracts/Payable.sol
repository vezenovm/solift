// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

// A payable address as a parameter and as a read, which the link test must declare as payable to pass on.
contract Payable {
    address payable internal recipient;

    function setRecipient(address payable _recipient) external {
        recipient = _recipient;
    }

    function paid(address payable _to, uint256 _a) external view returns (uint256) {
        return _to == recipient ? 0 : _a;
    }
}
