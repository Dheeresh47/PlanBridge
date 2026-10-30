"""
Classifying a scoring result into a routing outcome and its reasons.

Pure: this module consumes a ScoringResult and a MatcherConfig and returns a
decision. It touches no database, writes no prose and computes no score.

Every applicable condition is evaluated independently and collected as a
reason. Only then is the primary outcome chosen, as the first applicable
condition in the contract's precedence. Reason survival is therefore
structural rather than special-cased: an identifier conflict or a not-found
identifier stays in the reason list even when a higher-precedence outcome is
reported, so identifier evidence can never silently mask text evidence.

No decision anywhere else in the matcher uses `accept_floor` or `margin`;
the explanation layer prints them as evidence but decides nothing with
them. An unavailable top score or margin is never coerced to zero: it fails
every threshold condition instead of comparing as a measured zero.
"""

from dataclasses import dataclass

from app.matching import (
    IDENTIFIER_ABSENT,
    IDENTIFIER_AGREES,
    IDENTIFIER_CONFLICT,
    IDENTIFIER_MATCHED_WITHOUT_TEXT,
    IDENTIFIER_NOT_FOUND,
    ScoringResult,
)
from app.matching_config import MatcherConfig, current_matcher_config


NO_CANDIDATES = "no_candidates"
INSUFFICIENT_TEXT = "insufficient_text"
LOW_CONFIDENCE = "low_confidence"
AMBIGUOUS = "ambiguous"
SINGLE_STRONG_CANDIDATE = "single_strong_candidate"

#: The contract's primary-outcome precedence, highest first.
OUTCOME_PRECEDENCE = (
    NO_CANDIDATES,
    INSUFFICIENT_TEXT,
    IDENTIFIER_CONFLICT,
    IDENTIFIER_NOT_FOUND,
    LOW_CONFIDENCE,
    AMBIGUOUS,
    SINGLE_STRONG_CANDIDATE,
)

#: Reasons that describe identifier evidence without ever being an outcome.
INFORMATIONAL_REASONS = (
    IDENTIFIER_ABSENT,
    IDENTIFIER_AGREES,
    IDENTIFIER_MATCHED_WITHOUT_TEXT,
)

#: Reporting order for the reason list: outcome conditions in precedence
#: order, then informational reasons. Fixed so the list is deterministic.
REASON_ORDER = OUTCOME_PRECEDENCE + INFORMATIONAL_REASONS


class RoutingError(RuntimeError):
    """Raised when no outcome applies, which should be unreachable."""


@dataclass(frozen=True)
class RoutingDecision:
    """One event's outcome, its reasons, and the evidence behind them."""

    outcome: str
    #: Every applicable reason, including those whose outcome was outranked.
    reasons: tuple[str, ...]
    #: True for every outcome except a single strong candidate. Escalation
    #: is not approval: every outcome needs human approval before a
    #: schedule update.
    escalated: bool
    #: None means unavailable, which is not the same as zero.
    top_score: float | None
    margin: float | None
    matcher_version: str

    def has_reason(self, reason: str) -> bool:
        return reason in self.reasons


def route(
    result: ScoringResult,
    config: MatcherConfig | None = None,
) -> RoutingDecision:
    """Classify a scoring result without altering it."""
    config = config or current_matcher_config()

    applicable: set[str] = set()

    if result.eligible_count == 0:
        applicable.add(NO_CANDIDATES)

    if not result.text_usable:
        applicable.add(INSUFFICIENT_TEXT)

    # The scorer sets these two states only where the contract permits them,
    # so no further precondition is re-checked here.
    if result.identifier_state == IDENTIFIER_CONFLICT:
        applicable.add(IDENTIFIER_CONFLICT)

    if result.identifier_state == IDENTIFIER_NOT_FOUND:
        applicable.add(IDENTIFIER_NOT_FOUND)

    if result.identifier_state in INFORMATIONAL_REASONS:
        applicable.add(result.identifier_state)

    scored_leader = result.top_score is not None and result.margin is not None

    if scored_leader:
        if result.top_score < config.accept_floor:
            applicable.add(LOW_CONFIDENCE)
        elif result.margin < config.margin:
            applicable.add(AMBIGUOUS)
        else:
            # A conflicting or absent named activity is unresolved evidence,
            # so it withholds the strong-candidate outcome.
            blocked = applicable & {
                IDENTIFIER_CONFLICT,
                IDENTIFIER_NOT_FOUND,
            }

            if not blocked:
                applicable.add(SINGLE_STRONG_CANDIDATE)

    outcome = next(
        (name for name in OUTCOME_PRECEDENCE if name in applicable),
        None,
    )

    if outcome is None:
        raise RoutingError(
            "No routing outcome applies to this result; the scoring layer "
            "produced a state the routing contract does not cover."
        )

    reasons = tuple(name for name in REASON_ORDER if name in applicable)

    return RoutingDecision(
        outcome=outcome,
        reasons=reasons,
        escalated=outcome != SINGLE_STRONG_CANDIDATE,
        top_score=result.top_score,
        margin=result.margin,
        matcher_version=config.version,
    )
