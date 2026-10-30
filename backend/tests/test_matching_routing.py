"""
Tests for routing classification.

No database, no prose: these cover every outcome, the precedence between
them, and the reasons that survive a higher-precedence outcome.
"""

import dataclasses
from uuid import UUID

import pytest

from app.matching import (
    IDENTIFIER_ABSENT,
    IDENTIFIER_AGREES,
    IDENTIFIER_CONFLICT,
    IDENTIFIER_MATCHED_WITHOUT_TEXT,
    IDENTIFIER_NOT_FOUND,
    CandidateActivity,
    ScoredCandidate,
    ScoringResult,
    score_candidates,
)
from app.matching_config import current_matcher_config
from app.matching_routing import (
    AMBIGUOUS,
    INFORMATIONAL_REASONS,
    INSUFFICIENT_TEXT,
    LOW_CONFIDENCE,
    NO_CANDIDATES,
    OUTCOME_PRECEDENCE,
    REASON_ORDER,
    SINGLE_STRONG_CANDIDATE,
    RoutingError,
    route,
)

CONFIG = current_matcher_config()


def activity(external_id, name="install roof membrane", index=1):
    return CandidateActivity(
        activity_id=UUID(int=index),
        external_id=external_id,
        name=name,
    )


def candidate(score, position, identifier_match=False, external_id="A-1"):
    return ScoredCandidate(
        activity=activity(external_id, index=position),
        fuzzy_score=score,
        tfidf_score=score,
        combined_score=score,
        scored_position=position,
        identifier_match=identifier_match,
    )


def result(
    scores=(0.90, 0.20),
    identifier_state=IDENTIFIER_ABSENT,
    text_usable=True,
    eligible_count=None,
    identifier_position=None,
):
    """A ScoringResult assembled directly, so routing is tested in isolation."""
    scored = tuple(
        candidate(
            score,
            index,
            identifier_match=(index == identifier_position),
            external_id=f"A-{index}",
        )
        for index, score in enumerate(scores, start=1)
    )

    top = scored[0].combined_score if scored else None

    if not scored or not text_usable:
        top = None
        margin = None
    elif len(scored) == 1:
        margin = top
    else:
        margin = round(top - scored[1].combined_score, 5)

    return ScoringResult(
        scored=scored if text_usable else (),
        stored=scored[:1] if (scored and text_usable) else (),
        top_score=top,
        margin=margin,
        identifier_state=identifier_state,
        text_usable=text_usable,
        matching_text="install roof membrane" if text_usable else "",
        eligible_count=(
            len(scores) if eligible_count is None else eligible_count
        ),
    )


# --------------------------------------------------- outcome reachability


def test_single_strong_candidate():
    decision = route(result(scores=(0.90, 0.20)), CONFIG)

    assert decision.outcome == SINGLE_STRONG_CANDIDATE
    assert decision.escalated is False
    assert decision.matcher_version == CONFIG.version


def test_low_confidence_when_the_leader_is_below_the_accept_floor():
    decision = route(result(scores=(0.40, 0.01)), CONFIG)

    assert decision.outcome == LOW_CONFIDENCE
    assert decision.escalated is True


def test_ambiguous_when_the_margin_is_too_small():
    decision = route(result(scores=(0.80, 0.75)), CONFIG)

    assert decision.outcome == AMBIGUOUS
    assert decision.escalated is True


def test_no_candidates_when_the_project_is_empty():
    decision = route(result(scores=(), eligible_count=0), CONFIG)

    assert decision.outcome == NO_CANDIDATES
    assert decision.top_score is None
    assert decision.margin is None


def test_insufficient_text_when_there_is_no_usable_token():
    decision = route(
        result(scores=(0.90, 0.20), text_usable=False), CONFIG
    )

    assert decision.outcome == INSUFFICIENT_TEXT
    assert decision.top_score is None


def test_identifier_conflict_outranks_the_score_outcomes():
    decision = route(
        result(
            scores=(0.90, 0.20),
            identifier_state=IDENTIFIER_CONFLICT,
            identifier_position=2,
        ),
        CONFIG,
    )

    assert decision.outcome == IDENTIFIER_CONFLICT
    assert decision.escalated is True


def test_identifier_not_found_outranks_the_score_outcomes():
    decision = route(
        result(scores=(0.90, 0.20), identifier_state=IDENTIFIER_NOT_FOUND),
        CONFIG,
    )

    assert decision.outcome == IDENTIFIER_NOT_FOUND
    assert decision.escalated is True


def test_every_outcome_is_reachable():
    reached = {
        route(result(scores=(), eligible_count=0), CONFIG).outcome,
        route(result(text_usable=False), CONFIG).outcome,
        route(
            result(
                identifier_state=IDENTIFIER_CONFLICT, identifier_position=2
            ),
            CONFIG,
        ).outcome,
        route(
            result(identifier_state=IDENTIFIER_NOT_FOUND), CONFIG
        ).outcome,
        route(result(scores=(0.40, 0.01)), CONFIG).outcome,
        route(result(scores=(0.80, 0.75)), CONFIG).outcome,
        route(result(scores=(0.90, 0.20)), CONFIG).outcome,
    }

    assert reached == set(OUTCOME_PRECEDENCE)


# ------------------------------------------------------------ precedence


def test_no_candidates_outranks_insufficient_text():
    decision = route(
        result(scores=(), text_usable=False, eligible_count=0), CONFIG
    )

    assert decision.outcome == NO_CANDIDATES
    assert INSUFFICIENT_TEXT in decision.reasons


def test_insufficient_text_outranks_identifier_not_found():
    decision = route(
        result(
            scores=(0.90, 0.20),
            text_usable=False,
            identifier_state=IDENTIFIER_NOT_FOUND,
        ),
        CONFIG,
    )

    assert decision.outcome == INSUFFICIENT_TEXT
    assert IDENTIFIER_NOT_FOUND in decision.reasons


def test_identifier_conflict_outranks_identifier_not_found():
    """Only one can hold, so precedence order itself is what is asserted."""
    assert OUTCOME_PRECEDENCE.index(IDENTIFIER_CONFLICT) < (
        OUTCOME_PRECEDENCE.index(IDENTIFIER_NOT_FOUND)
    )


def test_low_confidence_outranks_ambiguous():
    assert OUTCOME_PRECEDENCE.index(LOW_CONFIDENCE) < (
        OUTCOME_PRECEDENCE.index(AMBIGUOUS)
    )


def test_precedence_matches_the_contract_order():
    assert OUTCOME_PRECEDENCE == (
        "no_candidates",
        "insufficient_text",
        "identifier_conflict",
        "identifier_not_found",
        "low_confidence",
        "ambiguous",
        "single_strong_candidate",
    )


# ------------------------------------------------------- reason survival


def test_not_found_reason_survives_no_candidates():
    decision = route(
        result(
            scores=(),
            eligible_count=0,
            identifier_state=IDENTIFIER_NOT_FOUND,
        ),
        CONFIG,
    )

    assert decision.outcome == NO_CANDIDATES
    assert decision.reasons == (NO_CANDIDATES, IDENTIFIER_NOT_FOUND)


def test_absent_reason_is_recorded_under_no_candidates():
    decision = route(
        result(
            scores=(),
            eligible_count=0,
            identifier_state=IDENTIFIER_ABSENT,
        ),
        CONFIG,
    )

    assert decision.reasons == (NO_CANDIDATES, IDENTIFIER_ABSENT)


def test_matched_without_text_reason_survives_insufficient_text():
    decision = route(
        result(
            scores=(0.90,),
            text_usable=False,
            identifier_state=IDENTIFIER_MATCHED_WITHOUT_TEXT,
        ),
        CONFIG,
    )

    assert decision.outcome == INSUFFICIENT_TEXT
    assert IDENTIFIER_MATCHED_WITHOUT_TEXT in decision.reasons


def test_conflict_reason_survives_with_a_low_score():
    decision = route(
        result(
            scores=(0.40, 0.01),
            identifier_state=IDENTIFIER_CONFLICT,
            identifier_position=2,
        ),
        CONFIG,
    )

    assert decision.outcome == IDENTIFIER_CONFLICT
    # The low score is still reported, not hidden by the conflict.
    assert LOW_CONFIDENCE in decision.reasons


def test_conflict_reason_survives_with_an_ambiguous_margin():
    decision = route(
        result(
            scores=(0.80, 0.75),
            identifier_state=IDENTIFIER_CONFLICT,
            identifier_position=2,
        ),
        CONFIG,
    )

    assert decision.outcome == IDENTIFIER_CONFLICT
    assert AMBIGUOUS in decision.reasons


def test_agreement_is_recorded_but_changes_nothing():
    agreeing = route(
        result(
            scores=(0.90, 0.20),
            identifier_state=IDENTIFIER_AGREES,
            identifier_position=1,
        ),
        CONFIG,
    )
    silent = route(result(scores=(0.90, 0.20)), CONFIG)

    assert agreeing.outcome == silent.outcome == SINGLE_STRONG_CANDIDATE
    assert IDENTIFIER_AGREES in agreeing.reasons
    assert agreeing.top_score == silent.top_score
    assert agreeing.margin == silent.margin


def test_agreement_does_not_rescue_a_low_score():
    decision = route(
        result(
            scores=(0.40, 0.01),
            identifier_state=IDENTIFIER_AGREES,
            identifier_position=1,
        ),
        CONFIG,
    )

    assert decision.outcome == LOW_CONFIDENCE
    assert decision.escalated is True


def test_conflict_withholds_the_strong_candidate_outcome():
    """Scores clear both thresholds, but the conflict is unresolved."""
    scores = (0.90, 0.20)
    clean = route(result(scores=scores), CONFIG)
    conflicted = route(
        result(
            scores=scores,
            identifier_state=IDENTIFIER_CONFLICT,
            identifier_position=2,
        ),
        CONFIG,
    )

    assert clean.outcome == SINGLE_STRONG_CANDIDATE
    assert conflicted.outcome == IDENTIFIER_CONFLICT
    assert SINGLE_STRONG_CANDIDATE not in conflicted.reasons


def test_not_found_withholds_the_strong_candidate_outcome():
    decision = route(
        result(scores=(0.90, 0.20), identifier_state=IDENTIFIER_NOT_FOUND),
        CONFIG,
    )

    assert SINGLE_STRONG_CANDIDATE not in decision.reasons


def test_reasons_follow_the_declared_order_and_are_deterministic():
    scoring = result(
        scores=(0.80, 0.75),
        identifier_state=IDENTIFIER_CONFLICT,
        identifier_position=2,
    )

    first = route(scoring, CONFIG)
    second = route(scoring, CONFIG)

    assert first == second

    positions = [REASON_ORDER.index(reason) for reason in first.reasons]
    assert positions == sorted(positions)


def test_every_reason_is_a_declared_name():
    for scoring in (
        result(scores=(), eligible_count=0),
        result(text_usable=False),
        result(scores=(0.40, 0.01)),
        result(scores=(0.80, 0.75)),
        result(scores=(0.90, 0.20)),
    ):
        for reason in route(scoring, CONFIG).reasons:
            assert reason in REASON_ORDER


# ------------------------------------------------- unavailable, not zero


def test_an_unavailable_top_score_is_not_treated_as_zero():
    decision = route(
        result(scores=(), eligible_count=0, text_usable=True), CONFIG
    )

    # A zero score would have produced low_confidence.
    assert LOW_CONFIDENCE not in decision.reasons
    assert AMBIGUOUS not in decision.reasons
    assert decision.top_score is None


def test_an_unavailable_margin_is_not_treated_as_zero():
    """A zero margin would read as ambiguous; unavailable must not."""
    decision = route(result(text_usable=False), CONFIG)

    assert AMBIGUOUS not in decision.reasons
    assert decision.margin is None


# -------------------------------------------------------- sole candidate


def test_sole_candidate_is_never_ambiguous():
    decision = route(result(scores=(0.60,)), CONFIG)

    assert decision.outcome == SINGLE_STRONG_CANDIDATE
    assert AMBIGUOUS not in decision.reasons
    assert decision.margin == 0.60


def test_sole_candidate_below_the_accept_floor_is_low_confidence():
    decision = route(result(scores=(0.30,)), CONFIG)

    assert decision.outcome == LOW_CONFIDENCE


# ------------------------------------------- integration with the scorer


def test_routes_a_real_scoring_result():
    schedule = [
        activity("A-110", "Install roof membrane", 1),
        activity("A-101", "Pour foundation slab", 2),
    ]

    decision = route(
        score_candidates("install roof membrane", schedule), CONFIG
    )

    assert decision.outcome == SINGLE_STRONG_CANDIDATE
    assert decision.reasons == (SINGLE_STRONG_CANDIDATE, IDENTIFIER_ABSENT)


def test_routes_a_real_textless_result_with_a_named_activity():
    schedule = [activity("A-110", "Install roof membrane", 1)]

    scoring = score_candidates("   ", schedule, "A-110")
    decision = route(scoring, CONFIG)

    assert decision.outcome == INSUFFICIENT_TEXT
    assert IDENTIFIER_MATCHED_WITHOUT_TEXT in decision.reasons
    # The identifier-only row exists but can never be a strong match.
    assert len(scoring.stored) == 1
    assert SINGLE_STRONG_CANDIDATE not in decision.reasons


def test_routes_a_real_empty_project():
    decision = route(score_candidates("install roof", [], "A-1"), CONFIG)

    assert decision.outcome == NO_CANDIDATES
    assert IDENTIFIER_NOT_FOUND in decision.reasons


# ------------------------------------------------------------- config use


def test_thresholds_come_from_the_passed_config():
    lenient = dataclasses.replace(
        CONFIG, version="test-lenient", accept_floor=0.30, margin=0.05
    )
    # Leader clears the lenient accept floor but not its margin.
    scoring = result(scores=(0.40, 0.39))

    assert route(scoring, CONFIG).outcome == LOW_CONFIDENCE
    assert route(scoring, lenient).outcome == AMBIGUOUS
    assert route(scoring, lenient).matcher_version == "test-lenient"


def test_default_config_is_the_current_one():
    scoring = result(scores=(0.90, 0.20))

    assert route(scoring).matcher_version == CONFIG.version


def test_informational_reasons_are_never_outcomes():
    for reason in INFORMATIONAL_REASONS:
        assert reason not in OUTCOME_PRECEDENCE


def test_an_uncovered_state_raises_rather_than_guessing():
    broken = ScoringResult(
        scored=(),
        stored=(),
        top_score=None,
        margin=None,
        identifier_state="something-unmapped",
        text_usable=True,
        matching_text="install roof",
        eligible_count=3,
    )

    with pytest.raises(RoutingError, match="No routing outcome applies"):
        route(broken, CONFIG)
