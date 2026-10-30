"""
Tests for the explanation layer.

Explanations must be deterministic, grounded in the stored numbers, and free
of probability language.
"""

import re
from uuid import UUID

from app.matching import CandidateActivity, score_candidates
from app.matching_config import current_matcher_config
from app.matching_explanations import (
    EVIDENCE_DISCLAIMER,
    NO_SIMILARITY_COMPUTED,
    explain_candidate,
    explain_result,
    shared_terms,
)
from app.matching_routing import route

CONFIG = current_matcher_config()

#: Words that would present a ranking score as a calibrated probability.
BANNED = (
    "probability",
    "probable",
    "likelihood",
    "confidence",
    "confident",
    "certainty",
    "chance",
    "odds",
)


def activity(external_id, name, index=1):
    return CandidateActivity(
        activity_id=UUID(int=index), external_id=external_id, name=name
    )


ROOF = activity("A-110", "Install roof membrane", 1)
FOUNDATION = activity("A-101", "Pour foundation slab", 2)
GLAZING = activity("A-120", "Install window glazing", 3)
SCHEDULE = [ROOF, FOUNDATION, GLAZING]


def explained(text, candidates=None, reported=None):
    # `is None`, not `or`: an empty schedule is a case under test.
    schedule = SCHEDULE if candidates is None else candidates
    scoring = score_candidates(text, schedule, reported)
    decision = route(scoring, CONFIG)
    return scoring, decision


def assert_clean(prose):
    lowered = prose.lower()
    for word in BANNED:
        assert word not in lowered, f"{word!r} in: {prose}"


# ------------------------------------------------------------ determinism


def test_candidate_prose_is_identical_across_calls():
    scoring, decision = explained("install roof membrane")

    first = explain_candidate(scoring, scoring.stored[0], decision, CONFIG)
    second = explain_candidate(scoring, scoring.stored[0], decision, CONFIG)

    assert first == second


def test_result_prose_is_identical_across_calls():
    scoring, decision = explained("install roof membrane")

    assert explain_result(scoring, decision, CONFIG) == explain_result(
        scoring, decision, CONFIG
    )


def test_prose_is_stable_under_input_reordering():
    forward_scoring, forward_decision = explained(
        "install roof membrane", SCHEDULE
    )
    reverse_scoring, reverse_decision = explained(
        "install roof membrane", list(reversed(SCHEDULE))
    )

    assert explain_candidate(
        forward_scoring, forward_scoring.stored[0], forward_decision, CONFIG
    ) == explain_candidate(
        reverse_scoring, reverse_scoring.stored[0], reverse_decision, CONFIG
    )
    assert explain_result(
        forward_scoring, forward_decision, CONFIG
    ) == explain_result(reverse_scoring, reverse_decision, CONFIG)


def test_shared_terms_are_sorted():
    scoring, _ = explained("membrane install roof")

    terms = shared_terms(scoring, scoring.scored[0])

    assert terms == sorted(terms)
    assert terms == ["install", "membrane", "roof"]


# ------------------------------------------------------ evidence grounding


def test_every_stored_candidate_gets_non_empty_prose():
    for text in (
        "install roof membrane",
        "catering invoice approved",
        "install window glazing north elevation",
    ):
        scoring, decision = explained(text)

        for candidate in scoring.stored:
            prose = explain_candidate(scoring, candidate, decision, CONFIG)
            assert prose.strip()
            assert_clean(prose)


def test_prose_quotes_the_stored_numbers_exactly():
    scoring, decision = explained("install roof membrane")
    top = scoring.stored[0]

    prose = explain_candidate(scoring, top, decision, CONFIG)

    assert f"{top.combined_score:.5f}" in prose
    assert f"{top.fuzzy_score:.5f}" in prose
    assert f"{top.tfidf_score:.5f}" in prose
    assert CONFIG.version in prose


def test_prose_states_the_scored_position_out_of_the_full_set():
    scoring, decision = explained("install roof membrane")

    prose = explain_candidate(scoring, scoring.stored[0], decision, CONFIG)

    assert f"Ranked 1 of {len(scoring.scored)} candidates" in prose


def test_shared_terms_are_named_or_their_absence_stated():
    scoring, decision = explained("install roof membrane")
    leader = explain_candidate(
        scoring, scoring.stored[0], decision, CONFIG
    )
    assert "Shared terms: install, membrane, roof." in leader

    unrelated_scoring, unrelated_decision = explained("zzz qqq")
    tail = explain_candidate(
        unrelated_scoring,
        unrelated_scoring.stored[0],
        unrelated_decision,
        CONFIG,
    )
    assert "No term is shared with the event text." in tail


def test_leader_prose_reports_the_margin():
    scoring, decision = explained("install roof membrane")

    prose = explain_candidate(scoring, scoring.stored[0], decision, CONFIG)

    assert f"Leads the next candidate by {scoring.margin:.5f}." in prose


def test_sole_candidate_prose_says_there_is_no_competitor():
    scoring, decision = explained("install roof membrane", [ROOF])

    prose = explain_candidate(scoring, scoring.stored[0], decision, CONFIG)

    assert "No competing candidate was scored" in prose
    assert "Leads the next candidate" not in prose


# ---------------------------------------------------- no probability talk


def test_no_probability_language_in_candidate_prose():
    scoring, decision = explained("install roof membrane", SCHEDULE, "A-101")

    for candidate in scoring.stored:
        assert_clean(explain_candidate(scoring, candidate, decision, CONFIG))


def test_no_probability_language_in_result_prose():
    for text, reported in (
        ("install roof membrane", None),
        ("install roof membrane", "A-101"),
        ("install roof membrane", "Z-999"),
        ("catering invoice approved", None),
        ("   ", "A-120"),
        ("   ", None),
    ):
        scoring, decision = explained(text, SCHEDULE, reported)
        assert_clean(explain_result(scoring, decision, CONFIG))

    empty_scoring, empty_decision = explained("install roof", [], "A-1")
    assert_clean(explain_result(empty_scoring, empty_decision, CONFIG))


def test_the_disclaimer_is_attached_to_scored_candidates():
    scoring, decision = explained("install roof membrane")

    prose = explain_candidate(scoring, scoring.stored[0], decision, CONFIG)

    assert prose.endswith(EVIDENCE_DISCLAIMER)


# --------------------------------------------------- identifier evidence


def test_agreeing_identifier_is_named_in_the_prose():
    scoring, decision = explained("install roof membrane", SCHEDULE, "A-110")

    prose = explain_candidate(scoring, scoring.stored[0], decision, CONFIG)

    assert "The report names this activity." in prose


def test_conflict_annotates_both_the_leader_and_the_named_activity():
    scoring, decision = explained("install roof membrane", SCHEDULE, "A-101")

    leader = next(c for c in scoring.stored if c.scored_position == 1)
    named = next(c for c in scoring.stored if c.identifier_match)

    leader_prose = explain_candidate(scoring, leader, decision, CONFIG)
    named_prose = explain_candidate(scoring, named, decision, CONFIG)

    assert "Outranks A-101, which the report names." in leader_prose
    assert "another candidate ranks above it on text" in named_prose


def test_result_prose_reports_a_not_found_identifier():
    scoring, decision = explained("install roof membrane", SCHEDULE, "Z-999")

    summary = explain_result(scoring, decision, CONFIG)

    assert "no eligible activity in this project matches" in summary
    assert "Routed to human review." in summary


def test_result_prose_reports_a_conflict():
    scoring, decision = explained("install roof membrane", SCHEDULE, "A-101")

    summary = explain_result(scoring, decision, CONFIG)

    assert "the text ranking does not lead with" in summary


# ------------------------------------------------------- textless events


def test_identifier_only_row_says_no_similarity_was_computed():
    scoring, decision = explained("   ", SCHEDULE, "A-120")

    row = scoring.stored[0]
    prose = explain_candidate(scoring, row, decision, CONFIG)

    assert NO_SIMILARITY_COMPUTED in prose
    assert "A-120" in prose
    assert "Human review is required." in prose
    # No rank is claimed, because nothing was ranked.
    assert "Ranked" not in prose
    assert_clean(prose)


def test_identifier_only_row_quotes_no_scores():
    scoring, decision = explained("", SCHEDULE, "A-120")

    prose = explain_candidate(scoring, scoring.stored[0], decision, CONFIG)

    assert "0.00000" not in prose
    assert not re.search(r"\d\.\d{5}", prose)


def test_textless_result_prose_says_scores_are_unavailable():
    scoring, decision = explained("   ", SCHEDULE, "A-120")

    summary = explain_result(scoring, decision, CONFIG)

    assert "no usable text" in summary
    assert "unavailable, not zero" in summary
    assert "there is no ranking for it to agree or conflict with" in summary


def test_empty_project_result_prose():
    scoring, decision = explained("install roof", [], None)

    summary = explain_result(scoring, decision, CONFIG)

    assert "no planned activity to match against" in summary
    assert "unavailable, not zero" in summary


# ------------------------------------------------------------- summaries


def test_result_prose_reports_scored_and_retained_counts():
    scoring, decision = explained("install roof membrane")

    summary = explain_result(scoring, decision, CONFIG)

    assert (
        f"Scored {len(scoring.scored)} of {scoring.eligible_count} "
        f"eligible activities and retained {len(scoring.stored)} for review."
    ) in summary


def test_result_prose_names_the_thresholds_it_compared_against():
    scoring, decision = explained("install roof membrane")

    summary = explain_result(scoring, decision, CONFIG)

    assert f"accept floor of {CONFIG.accept_floor:.2f}" in summary
    assert f"required {CONFIG.margin:.2f}" in summary


def test_strong_candidate_summary_still_requires_approval():
    scoring, decision = explained("install roof membrane")

    assert decision.escalated is False
    assert "requires human approval" in explain_result(
        scoring, decision, CONFIG
    )


def test_escalated_summary_says_so():
    scoring, decision = explained("catering invoice approved")

    assert decision.escalated is True
    assert "Routed to human review." in explain_result(
        scoring, decision, CONFIG
    )
