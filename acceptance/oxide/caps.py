"""The spec of Caps.getCurrentAvailable, the deposit capacity of the portal, and its theorems.

The capacity refills at RATE per second up to GLOBAL_LIMIT. Each deposit calls _markUsage, which reads
getCurrentAvailable before it writes $lastUpdatedTime and $cachedAvailable. Two facts about that state are hypotheses
here, not theorems, because the tool does not model storage writes: $lastUpdatedTime is at most block.timestamp, as
_markUsage and the constructor write block.timestamp; and $cachedAvailable is at most GLOBAL_LIMIT, as the constructor
writes GLOBAL_LIMIT and _markUsage writes available - amount, where available is at most GLOBAL_LIMIT by the first
theorem.
"""

import z3

from acceptance.oxide.project import PROJECT
from z3sol.link import Link
from z3sol.run import Spec

AVAILABLE = Spec(
    name="caps-available",
    target="src/core/Caps.sol:Caps.getCurrentAvailable",
    reads={
        "RATE": "rate",
        "GLOBAL_LIMIT": "globalLimit",
        "$lastUpdatedTime": "lastUpdatedTime",
        "$cachedAvailable": "cachedAvailable",
        "block.timestamp": "blockTimestamp",
    },
    link=Link(
        setup="proofs/subsidies/test/CapsSetup.sol:CapsSetup",
        call="caps.getCurrentAvailable()",
        adapters={
            ("rate", "globalLimit", "lastUpdatedTime", "cachedAvailable"): (
                "_deploy(rate, globalLimit, lastUpdatedTime, cachedAvailable);"
            ),
            "blockTimestamp": "vm.warp(blockTimestamp);",
        },
    ),
    project=PROJECT,
)


def available(q):
    return q.result > 0


def available_limit(q):
    return q.globalLimit > 0


def kept_state(q):
    """$lastUpdatedTime is at most block.timestamp, and $cachedAvailable at most GLOBAL_LIMIT"""
    return z3.And(q.lastUpdatedTime <= q.blockTimestamp, q.cachedAvailable <= q.globalLimit)


def deployed_range(q):
    """the kept state, with RATE and GLOBAL_LIMIT below 2**128 and block.timestamp below 2**64. Every seed in Oxide's
    environments that sets them has RATE below 2**61 and GLOBAL_LIMIT below 2**77; the sky-prod seed is pending."""
    return z3.And(kept_state(q), q.rate < 2**128, q.globalLimit < 2**128, q.blockTimestamp < 2**64)


def full_refill(q):
    """the kept state, and the refill since $lastUpdatedTime reaches GLOBAL_LIMIT"""
    return z3.And(kept_state(q), (q.blockTimestamp - q.lastUpdatedTime) * q.rate >= q.globalLimit)


@AVAILABLE.theorem(assume=("no-revert",), witness=available)
def available_at_most_the_global_limit(q):
    """the capacity is at most GLOBAL_LIMIT, for every state"""
    return q.result <= q.globalLimit


@AVAILABLE.theorem(assume=("no-revert",), given=full_refill, witness=available)
def available_refills_to_the_global_limit(q):
    """once the refill since the last deposit reaches GLOBAL_LIMIT, the capacity is GLOBAL_LIMIT"""
    return q.result == q.globalLimit


def later(q):
    """the kept state, and a later block.timestamp below 2**256"""
    t = q.free("laterTimestamp")
    return z3.And(kept_state(q), t >= q.blockTimestamp, t < 2**256)


@AVAILABLE.theorem(assume=("no-revert",), given=later, witness=available)
def available_does_not_fall_over_time(q):
    """with no deposit in between, the capacity later is at least the capacity now, or the later call reverts"""
    then = q.at(blockTimestamp=q.free("laterTimestamp"))
    return z3.Or(then.reverts, then.value >= q.result)


@AVAILABLE.theorem(given=deployed_range, witness=available)
def available_does_not_revert(q):
    """liveness: in the deployed range, getCurrentAvailable does not revert, so _markUsage reverts only on its own
    checks"""
    return z3.Not(q.reverts)


def largest_rate(q):
    """the kept state, RATE = type(uint256).max, as a deployment that turns the rate limit off would set it, some
    capacity left, and at least one second since $lastUpdatedTime"""
    return z3.And(
        kept_state(q),
        q.rate == 2**256 - 1,
        q.cachedAvailable >= 1,
        q.blockTimestamp >= q.lastUpdatedTime + 1,
    )


# The hazard that the deployed range leaves out. Each later deposit reverts too, in _markUsage before it writes
# $lastUpdatedTime, and RATE is immutable, so the portal takes no more deposits. Oxide's seed parser and deploy scripts
# check only that RATE is a decimal number.
@AVAILABLE.theorem(given=largest_rate, witness=available_limit)
def available_reverts_for_the_largest_rate(q):
    """with RATE = type(uint256).max, getCurrentAvailable reverts from one second after a deposit that leaves some
    capacity"""
    return q.reverts
