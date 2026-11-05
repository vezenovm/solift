// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {IParams, Structs, Window} from "tests/fixtures/contracts/Structs.sol";

contract ParamsStub is IParams {
    Window internal stored;
    uint256[3] internal values;

    function set(uint256 _start, uint256 _end) external {
        stored = Window(_start, _end);
    }

    function setTriple(uint256 _a, uint256 _b, uint256 _c) external {
        values = [_a, _b, _c];
    }

    function window() external view returns (Window memory) {
        return stored;
    }

    function triple() external view returns (uint256, uint256, uint256) {
        return (values[0], values[1], values[2]);
    }
}

contract StructsSetup is Test {
    Structs internal structs;
    ParamsStub internal params;

    function setUp() public {
        structs = new Structs();
        params = new ParamsStub();
        structs.setParams(params);
    }
}
