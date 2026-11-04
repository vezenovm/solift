// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

struct Span {
    uint256 start;
    uint256 end;
}

interface ICombinedQuotes {
    function quote(uint256 n) external view returns (uint256);
}

library Spans {
    function opened(Span memory _s, uint256 _t) internal pure returns (uint256) {
        return _t < _s.start ? 0 : _t - _s.start;
    }
}

// The extractor's features together: a key on an assigned local in a callee that takes a struct, a cut that takes new
// memory beside an inlined struct callee, and a modifier whose check reads a key.
contract Combined {
    ICombinedQuotes internal quotes;
    address internal keeper;

    function _quoteFor(Span memory _s) internal view returns (uint256) {
        uint256 amount = _s.start;
        amount = amount + _s.end;
        return quotes.quote(amount);
    }

    function quotedOnce(uint256 _a, uint256 _b) external view returns (uint256) {
        return _quoteFor(Span(_a, _b));
    }

    function quotedTwice(uint256 _a, uint256 _b) external view returns (uint256) {
        return _quoteFor(Span(_a, _b)) + _quoteFor(Span(_b, _a));
    }

    function inlinedAndCut(uint256 _s, uint256 _t) external pure returns (uint256) {
        Span memory w = Span(_s, _s + 1);
        return Spans.opened(w, _t) + Spans.opened(Span(_t, _t), _s);
    }

    function _checkQuote(uint256 _n) internal view {
        require(quotes.quote(_n) == 0);
    }

    modifier quoted(uint256 _n) {
        _checkQuote(_n);
        _;
    }

    function guarded(uint256 _x) external view quoted(_x) returns (uint256) {
        return _x;
    }

    function guardedAndChecked(uint256 _x) external view quoted(_x) returns (uint256) {
        _checkQuote(_x + 1);
        return _x;
    }
}
