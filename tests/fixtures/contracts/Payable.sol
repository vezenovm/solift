// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

struct Payee {
    address payable to;
    uint256 cap;
}

interface IPayees {
    function payee() external view returns (Payee memory);
}

library Payees {
    function pays(Payee memory _p, address _to) internal pure returns (bool) {
        return _p.to == _to;
    }
}

// A payable address as a parameter and as a read, which the link test must declare as payable to pass on: also as a
// field of a storage struct that a callee takes, and of a struct that an external call returns.
contract Payable {
    address payable internal recipient;
    Payee internal payee;
    IPayees internal payees;

    function setRecipient(address payable _recipient) external {
        recipient = _recipient;
    }

    function paid(address payable _to, uint256 _a) external view returns (uint256) {
        return _to == recipient ? 0 : _a;
    }

    function paidToPayee(address _to, uint256 _a) external view returns (uint256) {
        return Payees.pays(payee, _to) ? 0 : _a;
    }

    function paidToExternal(address _to, uint256 _a) external view returns (uint256) {
        Payee memory p = payees.payee();
        return p.to == _to ? 0 : _a;
    }
}
