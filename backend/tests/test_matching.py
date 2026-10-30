"""
Tests for the pure scoring layer.

No database, no routing: these cover normalization, the two component
scores, the total order, the full-set top score and margin, candidate
pruning, and identifier evidence.
"""

import dataclasses
from uuid import UUID, uuid4

import pytest

from app.matching import (
    IDENTIFIER_ABSENT,
    IDENTIFIER_AGREES,
    IDENTIFIER_CONFLICT,
    IDENTIFIER_MATCHED_WITHOUT_TEXT,
    IDENTIFIER_NOT_FOUND,
    CandidateActivity,
    assemble_result,
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


# ----------------------------------------- shared assembly path (step 1)


def evidence_for(event_text, candidates, reported=None):
    """
    Recompute component scores the way a cache would hold them.

    Mirrors what evaluation will do: normalize once, sort once, compute the
    two component scores, then hand them to the shared assembly path.
    """
    from app.matching import (
        _fuzzy_score,
        _normalized_identifier,
        _tfidf_scores,
        candidate_document,
    )

    event = matching_text(event_text)
    ordered = sorted(
        candidates,
        key=lambda a: (a.external_id, str(a.activity_id)),
    )
    reported_normalized = _normalized_identifier(reported)
    named = next(
        (
            a
            for a in ordered
            if _normalized_identifier(a.external_id) == reported_normalized
        ),
        None,
    )

    if not event:
        return event, ordered, [], [], named, bool(reported_normalized)

    documents = [candidate_document(a) for a in ordered]
    tfidf = _tfidf_scores(event, documents)
    fuzzy = [_fuzzy_score(event, d) for d in documents]

    return event, ordered, fuzzy, tfidf, named, bool(reported_normalized)


#: Eight bays, so the scored set exceeds the stored-candidate limit.
MANY_BAYS = [
    activity(f"M-{index:03d}", f"install roof membrane bay {index}")
    for index in range(8)
]

#: Two activities with the same name, so the tie-break is exercised.
DELIBERATE_TIE = [
    activity("T-002", "paint handrail", activity_id=UUID(int=22)),
    activity("T-001", "paint handrail", activity_id=UUID(int=11)),
]

EQUIVALENCE_CASES = [
    ("install roof membrane", SCHEDULE, None),
    ("install roof membrane", SCHEDULE, "A-110"),
    ("install roof membrane", SCHEDULE, "A-101"),
    ("install roof membrane", SCHEDULE, "Z-999"),
    ("membrane roof instaled", SCHEDULE, None),
    ("catering invoice approved", SCHEDULE, None),
    ("install install install", SCHEDULE, None),
    ("install roof membrane", [ROOF], None),
    ("install roof membrane", [], None),
    ("install roof membrane", [], "A-110"),
    ("   ", SCHEDULE, None),
    ("   ", SCHEDULE, "A-120"),
    ("!!!", SCHEDULE, "Z-999"),
    ("", [], None),
    # More candidates than the stored limit, so the limit is exercised.
    ("install roof membrane bay 3", MANY_BAYS, None),
    ("install roof membrane bay 3", MANY_BAYS, "M-007"),
    # A genuine tie, resolved only by external_id then activity_id.
    ("paint handrail", DELIBERATE_TIE, None),
]


def test_assemble_result_reproduces_score_candidates():
    """The cached path must produce the identical complete result."""
    for text, candidates, reported in EQUIVALENCE_CASES:
        direct = score_candidates(text, candidates, reported)
        event, ordered, fuzzy, tfidf, named, supplied = evidence_for(
            text, candidates, reported
        )
        assembled = assemble_result(
            event,
            ordered,
            fuzzy,
            tfidf,
            named_activity=named,
            identifier_supplied=supplied,
            config=current_matcher_config(),
        )

        assert assembled == direct, (text, candidates, reported)


def test_equivalence_holds_across_every_grid_configuration():
    """
    All 420 cells, so no weighting or floor diverges between the paths.

    Equality is over the complete ScoringResult, not the scores alone.
    """
    checked = 0

    for fuzzy_weight in (0.30, 0.40, 0.50, 0.60, 0.70):
        for accept in (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
            for margin in (0.05, 0.10, 0.15, 0.20):
                for floor in (0.10, 0.15, 0.20):
                    config = dataclasses.replace(
                        current_matcher_config(),
                        version="test-grid",
                        fuzzy_weight=fuzzy_weight,
                        tfidf_weight=round(1 - fuzzy_weight, 2),
                        accept_floor=accept,
                        margin=margin,
                        candidate_floor=floor,
                    )

                    for text, candidates, reported in EQUIVALENCE_CASES:
                        direct = score_candidates(
                            text, candidates, reported, config
                        )
                        (
                            event,
                            ordered,
                            fuzzy,
                            tfidf,
                            named,
                            supplied,
                        ) = evidence_for(text, candidates, reported)
                        assembled = assemble_result(
                            event,
                            ordered,
                            fuzzy,
                            tfidf,
                            named_activity=named,
                            identifier_supplied=supplied,
                            config=config,
                        )

                        assert assembled == direct
                        checked += 1

    assert checked == 420 * len(EQUIVALENCE_CASES)


def test_assemble_result_reuses_evidence_across_configurations():
    """
    One evidence computation serves every weighting.

    This is the property the evaluator depends on: component scores do not
    depend on the configuration, only the combined score does.
    """
    event, ordered, fuzzy, tfidf, named, supplied = evidence_for(
        "install roof membrane", SCHEDULE
    )

    results = []

    for fuzzy_weight in (0.30, 0.50, 0.70):
        config = dataclasses.replace(
            current_matcher_config(),
            version="test-weight",
            fuzzy_weight=fuzzy_weight,
            tfidf_weight=round(1 - fuzzy_weight, 2),
        )
        results.append(
            assemble_result(
                event,
                ordered,
                fuzzy,
                tfidf,
                named_activity=named,
                identifier_supplied=supplied,
                config=config,
            )
        )

    # Component scores identical, combined scores differ with the weighting.
    for result in results:
        assert {
            c.activity.external_id: (c.fuzzy_score, c.tfidf_score)
            for c in result.scored
        } == {
            c.activity.external_id: (c.fuzzy_score, c.tfidf_score)
            for c in results[0].scored
        }

    def combined_for(result, external_id):
        return next(
            c.combined_score
            for c in result.scored
            if c.activity.external_id == external_id
        )

    # A-120 scores differently on the two components, so the weighting
    # moves its combined score. An exact match would not: fuzzy and TF-IDF
    # would both be 1.0 and every weighting would agree.
    partial = next(c for c in results[0].scored if c.activity is GLAZING)
    assert partial.fuzzy_score != partial.tfidf_score

    assert len({combined_for(r, "A-120") for r in results}) == 3


def test_assemble_result_rejects_misaligned_component_scores():
    """Guards the positional contract between scores and activities."""
    event, ordered, fuzzy, tfidf, named, supplied = evidence_for(
        "install roof membrane", SCHEDULE
    )

    with pytest.raises(ValueError, match="positionally aligned"):
        assemble_result(
            event,
            ordered,
            fuzzy[:-1],
            tfidf,
            named_activity=named,
            identifier_supplied=supplied,
            config=current_matcher_config(),
        )

    with pytest.raises(ValueError, match="positionally aligned"):
        assemble_result(
            event,
            ordered,
            fuzzy,
            tfidf[:-1],
            named_activity=named,
            identifier_supplied=supplied,
            config=current_matcher_config(),
        )


def test_assemble_result_defaults_to_the_current_configuration():
    event, ordered, fuzzy, tfidf, named, supplied = evidence_for(
        "install roof membrane", SCHEDULE
    )

    assert assemble_result(
        event,
        ordered,
        fuzzy,
        tfidf,
        named_activity=named,
        identifier_supplied=supplied,
    ) == score_candidates("install roof membrane", SCHEDULE)


def test_assemble_result_rejects_unsorted_activities():
    """
    Equal lengths cannot prove correspondence, so the order is checked.

    A cache that pairs scores with the wrong activity usually does so by
    passing the activities unsorted.
    """
    event, ordered, fuzzy, tfidf, named, supplied = evidence_for(
        "install roof membrane", SCHEDULE
    )

    for mangled in (
        list(reversed(ordered)),
        [ordered[1], ordered[0], ordered[2]],
    ):
        with pytest.raises(ValueError, match="must be sorted"):
            assemble_result(
                event,
                mangled,
                fuzzy,
                tfidf,
                named_activity=named,
                identifier_supplied=supplied,
                config=current_matcher_config(),
            )


def test_equivalence_cases_cover_the_required_scenarios():
    """Guards the coverage this equivalence suite is relied on for."""
    config = current_matcher_config()
    seen = {
        "multiple": False,
        "tie": False,
        "sole": False,
        "none": False,
        "textless_named": False,
        "textless_unnamed": False,
        "agrees": False,
        "conflict": False,
        "not_found": False,
        "pruned": False,
        "named_retained": False,
        "over_limit": False,
    }

    for text, candidates, reported in EQUIVALENCE_CASES:
        result = score_candidates(text, candidates, reported, config)
        scores = [c.combined_score for c in result.scored]

        if len(candidates) > 1:
            seen["multiple"] = True
        if len(candidates) == 1:
            seen["sole"] = True
        if not candidates:
            seen["none"] = True
        if scores and len(scores) != len(set(scores)):
            seen["tie"] = True
        if not result.text_usable:
            key = "textless_named" if reported else "textless_unnamed"
            seen[key] = True
        if result.identifier_state == IDENTIFIER_AGREES:
            seen["agrees"] = True
        if result.identifier_state == IDENTIFIER_CONFLICT:
            seen["conflict"] = True
        if result.identifier_state == IDENTIFIER_NOT_FOUND:
            seen["not_found"] = True
        if len(result.stored) < len(result.scored):
            seen["pruned"] = True
        if len(result.scored) > config.stored_candidates:
            seen["over_limit"] = True

        named = [c for c in result.stored if c.identifier_match]
        if named and named[0].scored_position is not None:
            below = named[0].combined_score < config.candidate_floor
            outside = named[0].scored_position > config.stored_candidates
            if below or outside:
                seen["named_retained"] = True

    missing = [name for name, found in seen.items() if not found]
    assert missing == [], f"equivalence cases miss: {missing}"
