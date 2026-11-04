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

struct One {
    uint256 v;
}

struct Nested {
    Window window;
}

interface IFeed {
    function quote(uint256 n) external view returns (uint256);
}

interface IParams {
    function window() external view returns (Window memory);
    function triple() external view returns (uint256, uint256, uint256);
}

library Windows {
    function opened(Window memory _w, uint256 _t) internal pure returns (uint256) {
        return _t < _w.start ? 0 : _t - _w.start;
    }
}

// Memory structs that the function builds, and their aliases, as one local for each field; and the cases that the
// extractor refuses, as the model could miss a write that an alias or assembly makes.
contract Structs {
    using Windows for Window;

    IFeed internal feed;
    IParams internal params;
    Window internal config;

    function setParams(IParams _params) external {
        params = _params;
    }

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

    // A struct passed whole to a callee, which takes a parameter for each field: a struct that the function builds,
    // through `using`; a parameter of the target; a storage struct, which the call copies; a memory copy; a constructor.
    function openedBuilt(uint256 _s, uint256 _t) external pure returns (uint256) {
        Window memory w = Window(_s, _s + 10);
        return w.opened(_t);
    }

    function openedParameter(Window memory _w, uint256 _t) external pure returns (uint256) {
        return Windows.opened(_w, _t);
    }

    function openedConfig(uint256 _t) external view returns (uint256) {
        return Windows.opened(config, _t);
    }

    function openedCopy(uint256 _t) external view returns (uint256) {
        Window memory w = config;
        return w.opened(_t) + w.end;
    }

    function openedConstructed(uint256 _s, uint256 _t) external pure returns (uint256) {
        return Windows.opened(Window(_s, _s), _t);
    }

    function _writes(Window memory _w) internal pure returns (uint256) {
        _w.end = 1;
        return _w.end;
    }

    // A callee that writes a field of its struct parameter, which the caller's struct would see.
    function writesParameter(uint256 _a) external pure returns (uint256) {
        Window memory w = Window(_a, _a);
        return _writes(w);
    }

    function _quotedParameter(Window memory _w) internal view returns (uint256) {
        return feed.quote(_w.end);
    }

    // _quotedParameter runs twice, and its read map key names a field of its struct parameter.
    function quotedParameterTwice(uint256 _x) external view returns (uint256) {
        uint256 a = _quotedParameter(Window(1, 1));
        uint256 b = _quotedParameter(Window(2, 2));
        return _x + a - b;
    }

    // Structs that internal calls return: as a declaration, a field of the call, a bound value, through another
    // callee, a copy of storage, a one-field struct, and the target itself.
    function window() public view returns (Window memory) {
        return Window({start: config.start, end: config.start + 10});
    }

    function fromInternal(uint256 _t) external view returns (uint256) {
        Window memory w = window();
        return _t < w.end ? 0 : 1;
    }

    function windowEnd(uint256 _x) external view returns (uint256) {
        return window().end + _x;
    }

    function owed(uint256 _t) external view returns (uint256) {
        return config.end - window().opened(_t);
    }

    function _nestedWindow() internal view returns (Window memory) {
        return window();
    }

    function nestedReturn(uint256 _t) external view returns (uint256) {
        return _nestedWindow().end + _t;
    }

    function _copyReturn() internal view returns (Window memory) {
        Window memory w = config;
        return w;
    }

    function copyReturned(uint256 _t) external view returns (uint256) {
        return _copyReturn().start + _t;
    }

    function _one(uint256 _x) internal pure returns (One memory) {
        return One(_x + 1);
    }

    function oneField(uint256 _x) external pure returns (uint256) {
        One memory o = _one(_x);
        return o.v;
    }

    function _id(Window memory _w) internal pure returns (Window memory) {
        return _w;
    }

    // A callee that returns its struct parameter: the two names are one memory.
    function returnsParameter(uint256 _a) external pure returns (uint256) {
        Window memory w = Window(_a, _a);
        Window memory v = _id(w);
        return v.end;
    }

    // A field of a constructor's struct, whose other field can revert.
    function projected(uint256 _x, uint256 _y) external pure returns (uint256) {
        return Window(_x, 1 / _y).start;
    }

    // A named struct return, which starts at zero.
    function namedWindow(uint256 _x) public pure returns (Window memory result) {
        if (_x == 0) {
            return result;
        }
        return Window(_x, _x + 1);
    }

    function namedWindowEnd(uint256 _x) external pure returns (uint256) {
        return namedWindow(_x).end;
    }

    // A struct-valued call that runs on one branch of ?:.
    function guardedCall(bool _c) external view returns (uint256) {
        return _c ? window().end : 0;
    }

    // A struct and a tuple from an external view call: each field or value that the function reads is a read map key.
    function fromExternal(uint256 _t) external view returns (uint256) {
        Window memory w = params.window();
        return _t < w.end ? 0 : 2;
    }

    function externalPassed(uint256 _t) external view returns (uint256) {
        Window memory w = params.window();
        return w.opened(_t);
    }

    function externalPart(uint256 _x) external view returns (uint256) {
        (, uint256 b,) = params.triple();
        return b + _x;
    }

    // A struct from an external call, then a cut whose assembly could write its memory.
    function externalSmashed() external view returns (uint256) {
        Window memory w = params.window();
        uint256 a = _smash();
        return w.end + a;
    }

    function externalWritten(uint256 _x) external view returns (uint256) {
        Window memory w = params.window();
        w.end = _x;
        return w.end;
    }

    // A cut whose assembly is marked memory-safe.
    function safe(uint256 _x) external pure returns (uint256) {
        Window memory w = Window(_x, _x);
        uint256 ignored = _safe();
        return w.end + ignored;
    }
}
