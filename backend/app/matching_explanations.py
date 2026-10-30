"""
Human-readable evidence for a ranking.

Pure and deterministic: the same inputs always produce the same sentences,
so a stored explanation can be regenerated and compared.

Explanations state evidence only. Scores are described as ranking evidence,
never as a probability, a likelihood or a degree of confidence that a match
is correct.
"""

from app.matching import (
    IDENTIFIER_AGREES,
    IDENTIFIER_CONFLICT,
    IDENTIFIER_MATCHED_WITHOUT_TEXT,
    IDENTIFIER_NOT_FOUND,
    ScoredCandidate,
    ScoringResult,
    candidate_document,
)
from app.matching_config import MatcherConfig, current_matcher_config
from app.matching_routing import RoutingDecision


EVIDENCE_DISCLAIMER = (
    "Scores are ranking evidence, not a measure of how likely this match "
    "is to be correct."
)

NO_SIMILARITY_COMPUTED = (
    "No similarity was computed, so these zeros are not a measured score."
)


def shared_terms(result: ScoringResult, candidate: ScoredCandidate) -> list[str]:
    """
    Terms present in both the event text and the candidate document.

    Sorted, so the rendered list is stable whatever order the terms appeared
    in and whatever order the candidates arrived in.
    """
    event_terms = set(result.matching_text.split())
    document_terms = set(candidate_document(candidate.activity).split())

    return sorted(event_terms & document_terms)


def _score_sentence(
    candidate: ScoredCandidate,
    config: MatcherConfig,
) -> str:
    return (
        f"Combined score {candidate.combined_score:.5f} under "
        f"{config.version} from fuzzy {candidate.fuzzy_score:.5f} and "
        f"TF-IDF {candidate.tfidf_score:.5f}."
    )


def _margin_sentence(result: ScoringResult) -> str:
    if len(result.scored) == 1:
        return (
            "No competing candidate was scored, so there is nothing to "
            "separate it from."
        )

    return (
        f"Leads the next candidate by {result.margin:.5f}."
        if result.margin is not None
        else ""
    )


def explain_candidate(
    result: ScoringResult,
    candidate: ScoredCandidate,
    decision: RoutingDecision,
    config: MatcherConfig | None = None,
) -> str:
    """Evidence for one stored candidate, as one paragraph."""
    config = config or current_matcher_config()

    sentences: list[str] = []

    if candidate.scored_position is None:
        # An identifier-only row: stored because the report named it.
        sentences.append(
            "Stored because the report names this activity's identifier "
            f"{candidate.activity.external_id}."
        )
        sentences.append(NO_SIMILARITY_COMPUTED)
        sentences.append(
            "The event has no usable text, so no candidate was ranked."
        )
        sentences.append("Human review is required.")
        return " ".join(sentences)

    sentences.append(
        f"Ranked {candidate.scored_position} of {len(result.scored)} "
        "candidates by score."
    )
    sentences.append(_score_sentence(candidate, config))

    terms = shared_terms(result, candidate)

    if terms:
        sentences.append("Shared terms: " + ", ".join(terms) + ".")
    else:
        sentences.append("No term is shared with the event text.")

    if candidate.scored_position == 1:
        margin = _margin_sentence(result)

        if margin:
            sentences.append(margin)

    if candidate.identifier_match:
        if decision.has_reason(IDENTIFIER_CONFLICT):
            sentences.append(
                "The report names this activity, but another candidate "
                "ranks above it on text."
            )
        elif decision.has_reason(IDENTIFIER_AGREES):
            sentences.append("The report names this activity.")
        else:
            sentences.append(
                "The report names this activity's identifier."
            )
    elif candidate.scored_position == 1 and decision.has_reason(
        IDENTIFIER_CONFLICT
    ):
        named = next(
            (c for c in result.scored if c.identifier_match), None
        )

        if named is not None:
            sentences.append(
                "Outranks "
                f"{named.activity.external_id}, which the report names."
            )

    sentences.append(EVIDENCE_DISCLAIMER)

    return " ".join(sentences)


def explain_result(
    result: ScoringResult,
    decision: RoutingDecision,
    config: MatcherConfig | None = None,
) -> str:
    """A summary of what the matcher found, and what it withheld."""
    config = config or current_matcher_config()

    sentences: list[str] = []

    if result.eligible_count == 0:
        sentences.append(
            "The project has no planned activity to match against."
        )
    elif not result.text_usable:
        sentences.append(
            "The statement has no usable text, so no candidate was scored."
        )
    else:
        sentences.append(
            f"Scored {len(result.scored)} of {result.eligible_count} "
            f"eligible activities and retained {len(result.stored)} for "
            "review."
        )

    if result.top_score is None:
        sentences.append(
            "The top score and the margin are unavailable, not zero."
        )
    else:
        sentences.append(
            f"Top score {result.top_score:.5f} against an accept floor of "
            f"{config.accept_floor:.2f}."
        )

        if result.margin is not None:
            sentences.append(
                f"Margin {result.margin:.5f} against a required "
                f"{config.margin:.2f}."
            )

    if decision.has_reason(IDENTIFIER_NOT_FOUND):
        sentences.append(
            "The report names an identifier that no eligible activity in "
            "this project matches."
        )

    if decision.has_reason(IDENTIFIER_CONFLICT):
        sentences.append(
            "The report names an activity that the text ranking does not "
            "lead with."
        )

    if decision.has_reason(IDENTIFIER_MATCHED_WITHOUT_TEXT):
        sentences.append(
            "The report names an eligible activity, but without usable "
            "text there is no ranking for it to agree or conflict with."
        )

    if decision.escalated:
        sentences.append("Routed to human review.")
    else:
        sentences.append(
            "One candidate stands out, and still requires human approval "
            "before any schedule update."
        )

    return " ".join(sentences)
