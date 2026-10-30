"""
Scoring planned activities against a progress event.

This module is pure: it takes the event text, the project's eligible
activities and the reported identifier, and returns scores and an ordering.
It touches no database, decides no routing outcome and writes no explanation
prose; those belong to the layers above it.

RapidFuzz and TF-IDF are complementary rather than two readings of the same
thing. RapidFuzz compares surface forms and weights every token equally, so
it tolerates typos and reordering. TF-IDF weights terms by how
discriminative they are within this project's own schedule vocabulary, so a
statement made of common words scores near zero against everything. Adding a
third lexical signal would double-count.
"""

import re
from dataclasses import dataclass
from uuid import UUID

from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer

from app.matching_config import (
    SCORE_DECIMALS,
    MatcherConfig,
    current_matcher_config,
)


IDENTIFIER_ABSENT = "identifier_absent"
IDENTIFIER_AGREES = "identifier_agrees"
IDENTIFIER_CONFLICT = "identifier_conflict"
IDENTIFIER_NOT_FOUND = "identifier_not_found"
#: The report names an eligible activity but the event has no usable text,
#: so there is no ranking for the identifier to agree or conflict with.
IDENTIFIER_MATCHED_WITHOUT_TEXT = "identifier_matched_without_text"

_NON_WORD = re.compile(r"[^0-9a-z]+")


@dataclass(frozen=True)
class CandidateActivity:
    """An eligible planned activity, as the scorer sees it."""

    activity_id: UUID
    external_id: str
    name: str
    description: str | None = None


@dataclass(frozen=True)
class ScoredCandidate:
    activity: CandidateActivity
    fuzzy_score: float
    tfidf_score: float
    combined_score: float
    #: 1-based position in the full scored set, or None when the candidate
    #: was never scored because the event had no usable text.
    scored_position: int | None
    #: True when the report's identifier names this activity.
    identifier_match: bool


@dataclass(frozen=True)
class ScoringResult:
    """Scores and ordering for one event, before routing."""

    #: Every eligible candidate, in the configured total order. Empty when
    #: the event has no usable text or the project has no activities.
    scored: tuple[ScoredCandidate, ...]
    #: The order-preserving subsequence of `scored` to persist.
    stored: tuple[ScoredCandidate, ...]
    #: None means unavailable, which is not the same as zero.
    top_score: float | None
    margin: float | None
    identifier_state: str
    #: False when the normalized matching text has no usable token.
    text_usable: bool
    #: The normalized text the scores were computed from.
    matching_text: str
    #: Eligible activities offered for scoring. Routing needs this to tell a
    #: textless event in an empty project from one in a populated project:
    #: both leave `scored` empty, and only the former is `no_candidates`.
    eligible_count: int


def matching_text(text: str | None) -> str:
    """
    Normalize text for matching: lowercase, punctuation to spaces, collapse.

    Idempotent over the duplicate-detection normalization, so applying it to
    a stored `normalized_text` is safe. Kept separate from the retained
    statement, which is never rewritten.
    """
    if not text:
        return ""

    return " ".join(_NON_WORD.sub(" ", text.lower()).split())


def candidate_document(activity: CandidateActivity) -> str:
    """The text a candidate is matched on: its name and description."""
    parts = [activity.name or ""]

    if activity.description:
        parts.append(activity.description)

    return matching_text(" ".join(parts))


def _round(value: float) -> float:
    return round(value, SCORE_DECIMALS)


def _fuzzy_score(event: str, document: str) -> float:
    if not event or not document:
        return 0.0

    # token_set_ratio tolerates word order and partial overlap, which is
    # what distinguishes it from the TF-IDF signal.
    return _round(fuzz.token_set_ratio(event, document) / 100.0)


def _tfidf_scores(event: str, documents: list[str]) -> list[float]:
    """
    Cosine similarity of the event against each candidate document.

    The vectorizer is fitted on this project's documents only, so the
    inverse document frequencies reflect the project's own vocabulary.
    """
    if not event or not any(documents):
        return [0.0] * len(documents)

    vectorizer = TfidfVectorizer()

    try:
        matrix = vectorizer.fit_transform(documents)
    except ValueError:
        # Every document normalized away to stop-token-only content.
        return [0.0] * len(documents)

    event_vector = vectorizer.transform([event])

    # Both sides are L2-normalized by default, so the dot product is the
    # cosine similarity.
    similarities = (matrix @ event_vector.T).toarray().ravel()

    return [_round(max(0.0, min(1.0, float(value)))) for value in similarities]


def _normalized_identifier(value: str | None) -> str:
    return (value or "").strip().lower()


def _input_order_key(activity: CandidateActivity):
    """The order activities must arrive in, so cached scores line up."""
    return (activity.external_id, str(activity.activity_id))


def _sort_key(candidate: ScoredCandidate):
    """The configured total order, as a sort key."""
    return (
        -candidate.combined_score,
        -candidate.tfidf_score,
        -candidate.fuzzy_score,
        candidate.activity.external_id,
        str(candidate.activity.activity_id),
    )


def score_candidates(
    event_text: str | None,
    candidates: list[CandidateActivity],
    reported_activity_id: str | None = None,
    config: MatcherConfig | None = None,
) -> ScoringResult:
    """
    Score and order a project's activities against one event.

    `candidates` must already be restricted to the event's own project; this
    function does not know about projects and performs no eligibility check.
    """
    config = config or current_matcher_config()

    event = matching_text(event_text)
    reported = _normalized_identifier(reported_activity_id)

    # Sorted before anything is computed so the TF-IDF vocabulary order and
    # every tie-break are independent of the order candidates arrived in.
    ordered_input = sorted(candidates, key=_input_order_key)

    named_activity = next(
        (
            activity
            for activity in ordered_input
            if _normalized_identifier(activity.external_id) == reported
        ),
        None,
    )
    identifier_supplied = bool(reported)
    identifier_found = named_activity is not None

    if not event:
        fuzzy_values: list[float] = []
        tfidf_values: list[float] = []
    else:
        documents = [
            candidate_document(activity) for activity in ordered_input
        ]
        tfidf_values = _tfidf_scores(event, documents)
        fuzzy_values = [
            _fuzzy_score(event, document) for document in documents
        ]

    return assemble_result(
        event,
        ordered_input,
        fuzzy_values,
        tfidf_values,
        named_activity=named_activity,
        identifier_supplied=identifier_supplied,
        config=config,
    )


def assemble_result(
    event: str,
    activities: list[CandidateActivity],
    fuzzy_values: list[float],
    tfidf_values: list[float],
    named_activity: CandidateActivity | None,
    identifier_supplied: bool,
    config: MatcherConfig | None = None,
) -> ScoringResult:
    """
    Build a scoring result from already-computed component scores.

    This is the single assembly path: ranking, rounding of the combined
    score, tie-breaking, the full-set top score and margin, candidate-floor
    pruning, identifier retention and the textless and no-candidate cases
    all live here. Evaluation reuses it with cached component scores so that
    no second implementation can drift from this one.

    `event` is already normalized, `activities` already sorted by
    `(external_id, activity_id)`, and `fuzzy_values` and `tfidf_values` are
    positionally aligned with `activities`. An empty `event` means no usable
    text, and the component lists are then ignored.
    """
    config = config or current_matcher_config()

    identifier_found = named_activity is not None

    if not event:
        # No usable text, so nothing is scored. The top score and the margin
        # are unavailable rather than zero.
        stored: tuple[ScoredCandidate, ...] = ()
        state = IDENTIFIER_ABSENT

        if identifier_supplied:
            state = (
                IDENTIFIER_MATCHED_WITHOUT_TEXT
                if identifier_found
                else IDENTIFIER_NOT_FOUND
            )

        if named_activity is not None:
            # Stored for transparency only. The zero scores mean that no
            # similarity was computed, not that it was computed as zero.
            stored = (
                ScoredCandidate(
                    activity=named_activity,
                    fuzzy_score=0.0,
                    tfidf_score=0.0,
                    combined_score=0.0,
                    scored_position=None,
                    identifier_match=True,
                ),
            )

        return ScoringResult(
            scored=(),
            stored=stored,
            top_score=None,
            margin=None,
            identifier_state=state,
            text_usable=False,
            matching_text=event,
            eligible_count=len(activities),
        )

    if len(fuzzy_values) != len(activities) or len(tfidf_values) != len(
        activities
    ):
        raise ValueError(
            "Component scores must be positionally aligned with the "
            "activities they describe."
        )

    # Equal lengths cannot prove correspondence, so the required input
    # order is checked too. A cache that pairs scores with the wrong
    # activity usually does so by passing them unsorted.
    if list(activities) != sorted(activities, key=_input_order_key):
        raise ValueError(
            "Activities must be sorted by external_id then activity_id "
            "before assembly, so that cached component scores correspond "
            "to the activities they were computed for."
        )

    unordered: list[ScoredCandidate] = []

    for activity, fuzzy, tfidf in zip(
        activities, fuzzy_values, tfidf_values
    ):
        combined = _round(
            config.fuzzy_weight * fuzzy + config.tfidf_weight * tfidf
        )

        unordered.append(
            ScoredCandidate(
                activity=activity,
                fuzzy_score=fuzzy,
                tfidf_score=tfidf,
                combined_score=combined,
                scored_position=None,
                identifier_match=activity is named_activity,
            )
        )

    ranked = sorted(unordered, key=_sort_key)

    scored = tuple(
        ScoredCandidate(
            activity=candidate.activity,
            fuzzy_score=candidate.fuzzy_score,
            tfidf_score=candidate.tfidf_score,
            combined_score=candidate.combined_score,
            scored_position=position,
            identifier_match=candidate.identifier_match,
        )
        for position, candidate in enumerate(ranked, start=1)
    )

    if not scored:
        return ScoringResult(
            scored=(),
            stored=(),
            top_score=None,
            margin=None,
            identifier_state=(
                IDENTIFIER_NOT_FOUND
                if identifier_supplied
                else IDENTIFIER_ABSENT
            ),
            text_usable=True,
            matching_text=event,
            eligible_count=len(activities),
        )

    top_score = scored[0].combined_score

    # With no next candidate there is nothing to be ambiguous against, so
    # the margin is the sole candidate's own score.
    margin = (
        top_score
        if len(scored) == 1
        else _round(top_score - scored[1].combined_score)
    )

    # Selected by index, then emitted in scored order, so the stored list is
    # always an order-preserving subsequence of the scored set.
    selected = {0}

    for index in range(1, min(config.stored_candidates, len(scored))):
        if scored[index].combined_score >= config.candidate_floor:
            selected.add(index)

    if named_activity is not None:
        # The named candidate is never pruned and never limited away.
        selected.update(
            index
            for index, candidate in enumerate(scored)
            if candidate.identifier_match
        )

    stored = tuple(scored[index] for index in sorted(selected))

    if not identifier_supplied:
        state = IDENTIFIER_ABSENT
    elif not identifier_found:
        state = IDENTIFIER_NOT_FOUND
    elif scored[0].identifier_match:
        state = IDENTIFIER_AGREES
    else:
        state = IDENTIFIER_CONFLICT

    return ScoringResult(
        scored=scored,
        stored=stored,
        top_score=top_score,
        margin=margin,
        identifier_state=state,
        text_usable=True,
        matching_text=event,
        eligible_count=len(activities),
    )
