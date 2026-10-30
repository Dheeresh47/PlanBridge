"""
Tests for the pure scoring layer.

No database, no routing: these cover normalization, the two component
scores, the total order, the full-set top score and margin, candidate
pruning, and identifier evidence.
"""

import dataclasses
from uuid import UUID, uuid4

from app.matching import (
    IDENTIFIER_ABSENT,
    IDENTIFIER_AGREES,
    IDENTIFIER_CONFLICT,
    IDENTIFIER_MATCHED_WITHOUT_TEXT,
    IDENTIFIER_NOT_FOUND,
    CandidateActivity,
    candidate_document,
    matching_text,
    score_candidates,
)
from app.matching_config import current_matcher_config
from app.progress_report_validation import comparable_text


def activity(external_id, name, description=None, activity_id=None):
    return CandidateActivity(
        activity_id=activity_id or UUID(int=abs(hash(external_id)) % (2**128)),
        external_id=external_id,
        name=name,
        description=description,
    )


def with_floor(candidate_floor: float, **overrides):
    """
    The provisional configuration with a different candidate floor.

    Pruning tests set the floor explicitly rather than relying on the
    provisional value: token overlap gives unrelated text a non-trivial
    fuzzy score, so the provisional floor of 0.15 sits inside that noise.
    The mechanism under test is the pruning, not the chosen threshold.
    """
    return dataclasses.replace(
        current_matcher_config(),
        version="test-floor",
        candidate_floor=candidate_floor,
        **overrides,
    )


ROOF = activity("A-110", "Install roof membrane")
FOUNDATION = activity("A-101", "Pour foundation slab")
GLAZING = activity("A-120", "Install window glazing")
SCHEDULE = [ROOF, FOUNDATION, GLAZING]


# --------------------------------------------------------- normalization


def test_matching_text_lowercases_and_collapses():
    assert matching_text("  Install   ROOF membrane. ") == (
        "install roof membrane"
    )


def test_matching_text_turns_punctuation_into_separators():
    assert matching_text("roof-membrane (north/south)") == (
        "roof membrane north south"
    )


def test_matching_text_keeps_digits():
    assert matching_text("Pour 12.5 m3 slab") == "pour 12 5 m3 slab"


def test_matching_text_is_idempotent():
    once = matching_text("Install Roof-Membrane!")
    assert matching_text(once) == once


def test_matching_text_is_idempotent_over_duplicate_normalization():
    """Applying it to a stored normalized_text must be safe."""
    stored = comparable_text("  Install   Roof Membrane  ")
    assert matching_text(stored) == matching_text(
        "  Install   Roof Membrane  "
    )


def test_empty_and_symbol_only_text_normalize_to_nothing():
    for value in (None, "", "   ", "---", "!!! ???"):
        assert matching_text(value) == ""


def test_candidate_document_includes_the_description():
    document = candidate_document(
        activity("A-1", "Install roof", "Membrane and flashing")
    )
    assert document == "install roof membrane and flashing"


# ------------------------------------------------------- component scores


def test_exact_match_scores_at_the_top():
    result = score_candidates("Install roof membrane", SCHEDULE)

    assert result.text_usable is True
    assert result.scored[0].activity is ROOF
    assert result.scored[0].fuzzy_score == 1.0
    assert result.scored[0].tfidf_score > 0.9


def test_word_order_and_typos_still_rank_the_right_activity():
    result = score_candidates("membrane roof instaled", SCHEDULE)

    assert result.scored[0].activity is ROOF


def test_scores_are_within_zero_and_one_and_rounded():
    result = score_candidates("install window glazing north", SCHEDULE)

    for candidate in result.scored:
        for value in (
            candidate.fuzzy_score,
            candidate.tfidf_score,
            candidate.combined_score,
        ):
            assert 0.0 <= value <= 1.0
            assert round(value, 5) == value


def test_combined_score_is_the_configured_weighting():
    config = current_matcher_config()
    result = score_candidates("install roof membrane", SCHEDULE)

    for candidate in result.scored:
        expected = round(
            config.fuzzy_weight * candidate.fuzzy_score
            + config.tfidf_weight * candidate.tfidf_score,
            5,
        )
        assert candidate.combined_score == expected


def test_filler_text_scores_high_on_fuzzy_but_near_zero_on_tfidf():
    """
    The two signals are complementary, not two readings of one thing.

    'install' appears in two of three activities, so TF-IDF discounts it
    while token overlap does not.
    """
    result = score_candidates("install install install", SCHEDULE)

    top = result.scored[0]
    assert top.fuzzy_score > 0.4
    assert top.tfidf_score < top.fuzzy_score


def test_unrelated_text_scores_low_everywhere():
    result = score_candidates("catering invoice approved", SCHEDULE)

    assert result.top_score < current_matcher_config().accept_floor


def test_token_overlap_gives_unrelated_text_a_nonzero_floor():
    """
    Documents the noise level the thresholds have to clear.

    Unrelated English text still shares tokens and characters, so the fuzzy
    component is well above zero while TF-IDF is exactly zero. Development
    selection has to choose a candidate floor above this.
    """
    result = score_candidates("catering invoice approved", SCHEDULE)

    assert all(c.tfidf_score == 0.0 for c in result.scored)
    assert result.scored[0].fuzzy_score > 0.3
    assert 0.1 < result.top_score < 0.25


# ----------------------------------------------------------- ordering


def test_scored_positions_are_contiguous_and_ordered():
    result = score_candidates("install roof membrane", SCHEDULE)

    assert [c.scored_position for c in result.scored] == [1, 2, 3]
    scores = [c.combined_score for c in result.scored]
    assert scores == sorted(scores, reverse=True)


def test_ranking_is_independent_of_input_order():
    forward = score_candidates("install roof membrane", SCHEDULE)
    reversed_order = score_candidates(
        "install roof membrane", list(reversed(SCHEDULE))
    )

    def signature(result):
        return [
            (
                c.activity.external_id,
                c.fuzzy_score,
                c.tfidf_score,
                c.combined_score,
                c.scored_position,
            )
            for c in result.scored
        ]

    assert signature(forward) == signature(reversed_order)


def test_repeated_runs_are_identical():
    first = score_candidates("install window glazing", SCHEDULE)
    second = score_candidates("install window glazing", SCHEDULE)

    assert first == second


def test_ties_break_by_external_id_then_activity_id():
    """Two identical names must still order deterministically."""
    first = activity("B-002", "Paint handrail", activity_id=UUID(int=2))
    second = activity("B-001", "Paint handrail", activity_id=UUID(int=1))
    third = activity("B-001", "Paint handrail", activity_id=UUID(int=9))

    result = score_candidates("paint handrail", [first, second, third])

    scores = {c.combined_score for c in result.scored}
    assert len(scores) == 1, "expected a genuine tie"

    assert [
        (c.activity.external_id, c.activity.activity_id)
        for c in result.scored
    ] == [
        ("B-001", UUID(int=1)),
        ("B-001", UUID(int=9)),
        ("B-002", UUID(int=2)),
    ]


# -------------------------------------------------- top score and margin


def test_margin_is_the_gap_to_the_next_candidate():
    result = score_candidates("install roof membrane", SCHEDULE)

    assert result.top_score == result.scored[0].combined_score
    assert result.margin == round(
        result.scored[0].combined_score - result.scored[1].combined_score, 5
    )


def test_sole_candidate_margin_equals_its_own_score():
    """Contract: a sole candidate is unambiguous, not unavailable."""
    result = score_candidates("install roof membrane", [ROOF])

    assert len(result.scored) == 1
    assert result.margin == result.top_score
    assert result.margin is not None


def test_no_candidates_leaves_top_score_and_margin_unavailable():
    result = score_candidates("install roof membrane", [])

    assert result.scored == ()
    assert result.stored == ()
    assert result.top_score is None
    assert result.margin is None
    assert result.text_usable is True


def test_margin_is_computed_over_the_full_set_not_the_stored_list():
    """Pruning must not conceal a near tie."""
    near_ties = [
        activity("C-001", "paint handrail"),
        activity("C-002", "paint handrail"),
    ]

    # Unrelated text, so both tie well below the floor and only rank 1
    # survives pruning.
    result = score_candidates(
        "catering invoice approved", near_ties, config=with_floor(0.30)
    )

    assert len(result.stored) == 1
    # ...but the margin still reflects the tie in the scored set.
    assert len(result.scored) == 2
    assert result.scored[0].combined_score == (
        result.scored[1].combined_score
    )
    assert result.margin == 0.0


# ------------------------------------------------------------- pruning


def test_stored_list_is_limited_to_the_configured_count():
    many = [
        activity(f"D-{index:03d}", f"install roof membrane bay {index}")
        for index in range(12)
    ]

    result = score_candidates("install roof membrane bay 3", many)

    assert len(result.scored) == 12
    assert len(result.stored) == current_matcher_config().stored_candidates


def test_rank_one_is_stored_even_below_the_candidate_floor():
    config = with_floor(0.99, accept_floor=0.99)
    result = score_candidates(
        "catering invoice approved", SCHEDULE, config=config
    )

    assert result.top_score < config.candidate_floor
    assert len(result.stored) == 1
    assert result.stored[0] is result.scored[0]


def test_pruning_applies_below_rank_one_only():
    config = with_floor(0.30)
    result = score_candidates(
        "install roof membrane", SCHEDULE, config=config
    )

    # Rank 1 is always stored; everything after it cleared the floor.
    assert len(result.stored) >= 1
    for candidate in result.stored[1:]:
        assert candidate.combined_score >= config.candidate_floor

    # The third activity scores below the floor and is pruned.
    assert len(result.stored) < len(result.scored)


def test_stored_list_is_an_order_preserving_subsequence():
    many = [
        activity(f"E-{index:03d}", f"install roof membrane bay {index}")
        for index in range(12)
    ]

    result = score_candidates("install roof membrane bay 7", many)

    positions = [c.scored_position for c in result.stored]
    assert positions == sorted(positions)
    assert positions[0] == 1

    stored_ids = [c.activity.activity_id for c in result.stored]
    scored_ids = [c.activity.activity_id for c in result.scored]
    # Every stored candidate appears in the scored set, in the same order.
    assert stored_ids == [i for i in scored_ids if i in set(stored_ids)]


# -------------------------------------------------- identifier evidence


def test_identifier_absent_when_none_reported():
    result = score_candidates("install roof membrane", SCHEDULE)

    assert result.identifier_state == IDENTIFIER_ABSENT
    assert all(not c.identifier_match for c in result.scored)


def test_identifier_agrees_when_it_names_the_leader():
    result = score_candidates("install roof membrane", SCHEDULE, "A-110")

    assert result.identifier_state == IDENTIFIER_AGREES
    assert result.scored[0].identifier_match is True


def test_identifier_matching_is_case_and_space_insensitive():
    result = score_candidates("install roof membrane", SCHEDULE, "  a-110 ")

    assert result.identifier_state == IDENTIFIER_AGREES


def test_identifier_conflict_when_it_names_another_activity():
    result = score_candidates("install roof membrane", SCHEDULE, "A-101")

    assert result.identifier_state == IDENTIFIER_CONFLICT
    # The leader is unchanged: identifier evidence never reorders.
    assert result.scored[0].activity is ROOF
    named = next(c for c in result.scored if c.identifier_match)
    assert named.activity is FOUNDATION


def test_identifier_not_found_when_it_names_nothing_eligible():
    result = score_candidates("install roof membrane", SCHEDULE, "Z-999")

    assert result.identifier_state == IDENTIFIER_NOT_FOUND
    assert all(not c.identifier_match for c in result.scored)


def test_identifier_not_found_when_the_project_has_no_activities():
    result = score_candidates("install roof membrane", [], "A-110")

    assert result.identifier_state == IDENTIFIER_NOT_FOUND
    assert result.stored == ()


def test_named_candidate_is_stored_even_when_it_would_be_pruned():
    many = [
        activity(f"F-{index:03d}", f"install roof membrane bay {index}")
        for index in range(12)
    ]
    outsider = activity("Z-001", "catering invoice reconciliation")

    config = with_floor(0.30)
    result = score_candidates(
        "install roof membrane bay 3",
        many + [outsider],
        "Z-001",
        config=config,
    )

    named = next(c for c in result.stored if c.identifier_match)
    assert named.activity.external_id == "Z-001"
    # It is stored despite scoring below the floor and outside the limit.
    assert named.combined_score < config.candidate_floor
    assert named.scored_position > config.stored_candidates

    # The stored list remains an order-preserving subsequence.
    positions = [c.scored_position for c in result.stored]
    assert positions == sorted(positions)


def test_identifier_never_changes_the_top_score_or_margin():
    without = score_candidates("install roof membrane", SCHEDULE)
    with_conflict = score_candidates(
        "install roof membrane", SCHEDULE, "A-101"
    )

    assert with_conflict.top_score == without.top_score
    assert with_conflict.margin == without.margin
    assert [c.scored_position for c in with_conflict.scored] == [
        c.scored_position for c in without.scored
    ]


# --------------------------------------------------------- textless events


def test_textless_event_scores_nothing_and_reports_unavailable():
    result = score_candidates("   ", SCHEDULE)

    assert result.text_usable is False
    assert result.scored == ()
    assert result.stored == ()
    assert result.top_score is None
    assert result.margin is None
    assert result.identifier_state == IDENTIFIER_ABSENT


def test_textless_event_with_a_named_activity_stores_one_zero_score_row():
    result = score_candidates("!!!", SCHEDULE, "A-120")

    assert result.text_usable is False
    assert result.identifier_state == IDENTIFIER_MATCHED_WITHOUT_TEXT
    assert result.scored == ()

    assert len(result.stored) == 1
    row = result.stored[0]
    assert row.activity is GLAZING
    assert row.identifier_match is True
    # Zero means no similarity was computed, which is why the top score and
    # the margin stay unavailable rather than becoming zero.
    assert row.fuzzy_score == 0.0
    assert row.tfidf_score == 0.0
    assert row.combined_score == 0.0
    assert row.scored_position is None
    assert result.top_score is None
    assert result.margin is None


def test_textless_event_with_an_unknown_identifier_stores_nothing():
    result = score_candidates("", SCHEDULE, "Z-999")

    assert result.identifier_state == IDENTIFIER_NOT_FOUND
    assert result.stored == ()
    assert result.top_score is None
    assert result.margin is None


def test_a_zero_score_row_can_never_reach_the_accept_floor():
    result = score_candidates("", SCHEDULE, "A-120")

    assert result.stored[0].combined_score < (
        current_matcher_config().accept_floor
    )


def test_scoring_never_mutates_its_inputs():
    schedule = list(SCHEDULE)
    before = list(schedule)

    score_candidates("install roof membrane", schedule, "A-101")

    assert schedule == before
    assert all(isinstance(a.activity_id, UUID) for a in schedule)


def test_distinct_events_produce_distinct_leaders():
    roof = score_candidates("roof membrane installed", SCHEDULE)
    slab = score_candidates("foundation slab poured", SCHEDULE)

    assert roof.scored[0].activity is ROOF
    assert slab.scored[0].activity is FOUNDATION


def test_uuid_inputs_are_accepted_as_given():
    identifier = uuid4()
    only = activity("G-1", "install roof membrane", activity_id=identifier)

    result = score_candidates("install roof membrane", [only])

    assert result.scored[0].activity.activity_id == identifier
