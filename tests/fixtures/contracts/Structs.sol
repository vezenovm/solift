// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

struct Window {
    uint256 start;
    uint256 end;
}

struct Owner {
    address who;
    bool flag;
}

struct Nested {
    Window window;
}

interface IFeed {
    function quote(uint256 n) external view returns (uint256);
}

// Memory structs that the function builds, and their aliases, as one local for each field; and the cases that the
// extractor refuses, as the model could miss a write that an alias or assembly makes.
contract Structs {
    IFeed internal feed;
    Window internal config;

    function setConfig(uint256 _start, uint256 _end) external {
        config = Window(_start, _end);
    }

    function _wrapSmash() internal pure returns (uint256) {
        return _smash();
    }

    // A callee whose cut runs assembly, called before and after a struct exists: the extraction reuses its model.
    function smashedThroughCallee(uint256 _x) external pure returns (uint256) {
        uint256 a = _wrapSmash();
        Window memory w = Window(_x, _x);
        uint256 b = _wrapSmash();
        return w.end + a + b;
    }

    // A cut whose assembly runs before the struct exists, and could have moved the free memory pointer.
    function smashedBefore(uint256 _x) external pure returns (uint256) {
        uint256 a = _smash();
        Window memory w = Window(_x, _x);
        return w.end + a;
    }

    function _commented() internal pure returns (uint256) {
        /// This is not memory-safe-assembly.
        assembly {
            mstore(0x80, 7)
        }
        return 0;
    }

    // A comment that names the annotation without being it.
    function smashedCommented(uint256 _x) external pure returns (uint256) {
        Window memory w = Window(_x, _x);
        uint256 a = _commented();
        return w.end + a;
    }

    // The zero of each field type.
    function zeroOwner(uint256 _x) external pure returns (uint256) {
        Owner memory o;
        return o.who == address(0) && !o.flag ? _x : 0;
    }

    function q_start(uint256 _n) internal pure returns (uint256) {
        return _n + 1;
    }

    // q's field local would have the name of the function q_start, which the model calls.
    function shadowsAFunction(uint256 _n) external pure returns (uint256) {
        Window memory q = Window(_n, _n);
        return q_start(q.end);
    }

    function aliasedParameter(Window memory _w, uint256 _x) external pure returns (uint256) {
        Window memory v = _w;
        return v.start + _w.start + _x;
    }

    function built(uint256 _t) external pure returns (uint256) {
        Window memory w = Window({end: _t + 1, start: _t});
        return w.end - w.start;
    }

    function aliased(uint256 _a, uint256 _b) external pure returns (uint256) {
        Window memory w = Window(_a, _b);
        Window memory v = w;
        return v.start + w.end;
    }

    function zero(uint256 _x) external pure returns (uint256) {
        Window memory w;
        return w.end + _x;
    }

    function copyAliased() external view returns (uint256) {
        Window memory w = config;
        Window memory v = w;
        return v.end - w.start;
    }

    function copyAliasedAfterWrite(uint256 _x) external view returns (uint256) {
        Window memory w = config;
        Window memory v = w;
        w.end = _x;
        return v.end;
    }

    function fieldWritten(uint256 _a, uint256 _b) external pure returns (uint256) {
        Window memory w = Window(_a, _b);
        w.end = _a;
        return w.end;
    }

    function nested(uint256 _a) external pure returns (uint256) {
        Nested memory n = Nested(Window(_a, _a));
        return n.window.end;
    }

    function clash(uint256 _a) external pure returns (uint256) {
        Window memory w = Window(_a, _a);
        uint256 w_end = 1;
        return w.end + w_end;
    }

    function _quoted(uint256 _n) internal view returns (uint256) {
        Window memory w = Window(_n, _n + 1);
        return feed.quote(w.end);
    }

    // _quoted runs twice, and its read map key names a field of a struct that it builds from its parameter.
    function quotedTwice(uint256 _x) external view returns (uint256) {
        uint256 a = _quoted(1);
        uint256 b = _quoted(2);
        return _x + a - b;
    }

    function _smash() internal pure returns (uint256) {
        assembly {
            mstore(0x80, 7)
        }
        return 0;
    }

    function _safe() internal pure returns (uint256 r) {
        assembly ("memory-safe") {
            r := 0
        }
    }

    // A cut whose assembly could write w's memory.
    function smashed(uint256 _x) external pure returns (uint256) {
        Window memory w = Window(_x, _x);
        uint256 ignored = _smash();
        return w.end + ignored;
    }

    // A cut whose assembly is marked memory-safe.
    function safe(uint256 _x) external pure returns (uint256) {
        Window memory w = Window(_x, _x);
        uint256 ignored = _safe();
        return w.end + ignored;
    }
}
