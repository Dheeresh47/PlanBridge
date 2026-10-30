"""
Cached matching evidence for evaluation.

The development search tries 420 configurations over one dataset. The two
component scores do not depend on the configuration: only the combined
score, the ordering, the margin, the pruning and the routing do. So the
components are computed once and cached, and every configuration-dependent
result is assembled from them through the production assembly path.

Nothing here reimplements matching. The component scores come from the
production `_fuzzy_score` and `_tfidf_scores`, the text normalization from
`matching_text`, the candidate document from `candidate_document`, the named
activity from `resolve_named_activity`, and every result from
`assemble_result`. A faster evaluator that fitted TF-IDF differently, or
ordered candidates differently, would measure something other than the
matcher.

Nothing configuration-dependent is cached: no combined score, no ranking,
no margin, no pruning decision and no ScoringResult.
"""

import time
import tracemalloc
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from app.matching import (
    CandidateActivity,
    ScoringResult,
    _fuzzy_score,
    _input_order_key,
    _tfidf_scores,
    assemble_result,
    candidate_document,
    matching_text,
    resolve_named_activity,
)
from app.matching_config import MatcherConfig, current_matcher_config
from evaluation.synthetic_dataset import (
    SyntheticActivity,
    SyntheticDataset,
    SyntheticEvent,
    development_dataset,
)


class EvidenceCacheError(LookupError):
    """Raised when cached evidence is missing, foreign or inconsistent."""


@dataclass(frozen=True)
class PairEvidence:
    """The configuration-independent evidence for one event and activity."""

    event_id: UUID
    activity_id: UUID
    fuzzy_score: float
    tfidf_score: float


@dataclass(frozen=True)
class EventEvidence:
    """
    One event's evidence.

    `pairs` is empty for an event with no usable text. No zero-valued pair
    is fabricated: a missing pair means nothing was computed, which is not
    the same as a computed similarity of zero.

    The reported identifier is held here, beside the text evidence rather
    than inside it, so identifier evidence never enters a similarity score.
    """

    event_id: UUID
    project_id: UUID
    matching_text: str
    text_usable: bool
    reported_activity_id: str | None
    truth_activity_id: UUID | None
    pairs: Mapping[UUID, PairEvidence]


@dataclass(frozen=True)
class EvidenceCache:
    """Evidence for one split, keyed by identifier throughout."""

    split: str
    #: event_id -> evidence
    events: Mapping[UUID, EventEvidence]
    #: project_id -> activities, in the order assembly requires
    activities: Mapping[UUID, tuple[CandidateActivity, ...]]

    def event_ids(self) -> tuple[UUID, ...]:
        return tuple(self.events)

    def pair_count(self) -> int:
        return sum(len(evidence.pairs) for evidence in self.events.values())


@dataclass(frozen=True)
class EvidenceBenchmark:
    events: int
    pairs: int
    generation_seconds: float
    precompute_seconds: float
    cache_bytes: int


def to_candidate(activity: SyntheticActivity) -> CandidateActivity:
    """Convert a generated activity into what the matcher consumes."""
    return CandidateActivity(
        activity_id=activity.activity_id,
        external_id=activity.external_id,
        name=activity.name,
    )


def project_candidates(
    activities: tuple[SyntheticActivity, ...],
) -> tuple[CandidateActivity, ...]:
    """
    The project's activities in the order assembly requires.

    Sorted with the production key, so cached scores and the assembly see
    the same order.
    """
    return tuple(
        sorted((to_candidate(a) for a in activities), key=_input_order_key)
    )


def event_evidence_for(
    event_id: UUID,
    project_id: UUID,
    text: str,
    reported_activity_id: str | None,
    truth_activity_id: UUID | None,
    activities: tuple[CandidateActivity, ...],
) -> EventEvidence:
    """
    Compute one event's evidence against one project's activities.

    Used both when building a cache from a dataset and when a test needs
    evidence for a hand-built case, so there is one place where component
    scores are computed.
    """
    normalized = matching_text(text)

    if not normalized:
        # No usable text, so nothing is computed and no pair is invented.
        return EventEvidence(
            event_id=event_id,
            project_id=project_id,
            matching_text=normalized,
            text_usable=False,
            reported_activity_id=reported_activity_id,
            truth_activity_id=truth_activity_id,
            pairs=MappingProxyType({}),
        )

    documents = [candidate_document(activity) for activity in activities]
    tfidf_values = _tfidf_scores(normalized, documents)

    pairs = {
        activity.activity_id: PairEvidence(
            event_id=event_id,
            activity_id=activity.activity_id,
            fuzzy_score=_fuzzy_score(normalized, document),
            tfidf_score=tfidf,
        )
        for activity, document, tfidf in zip(
            activities, documents, tfidf_values
        )
    }

    return EventEvidence(
        event_id=event_id,
        project_id=project_id,
        matching_text=normalized,
        text_usable=True,
        reported_activity_id=reported_activity_id,
        truth_activity_id=truth_activity_id,
        pairs=MappingProxyType(pairs),
    )


def _check_event(
    evidence: EventEvidence,
    activities: tuple[CandidateActivity, ...],
) -> None:
    """Enforce the project boundary and the textless rule."""
    expected = {activity.activity_id for activity in activities}
    found = set(evidence.pairs)

    if not evidence.text_usable:
        if found:
            raise EvidenceCacheError(
                f"Event {evidence.event_id} has no usable text but carries "
                f"{len(found)} evidence pairs; a textless event must carry "
                "none rather than zero-valued pairs."
            )
        return

    foreign = found - expected

    if foreign:
        raise EvidenceCacheError(
            f"Event {evidence.event_id} carries evidence for "
            f"{len(foreign)} activity or activities outside its project."
        )

    missing = expected - found

    if missing:
        raise EvidenceCacheError(
            f"Event {evidence.event_id} is missing evidence for "
            f"{len(missing)} of its project's activities."
        )

    for activity_id, pair in evidence.pairs.items():
        if pair.activity_id != activity_id or pair.event_id != (
            evidence.event_id
        ):
            raise EvidenceCacheError(
                "Cached evidence is keyed inconsistently with its own "
                f"identifiers for event {evidence.event_id}."
            )


def build_evidence_cache(dataset: SyntheticDataset) -> EvidenceCache:
    """Precompute every event's component scores once."""
    activities = {
        project.project_id: project_candidates(project.activities)
        for project in dataset.projects
    }

    events: dict[UUID, EventEvidence] = {}

    for event in dataset.events:
        if event.project_id not in activities:
            raise EvidenceCacheError(
                f"Event {event.event_id} names an unknown project."
            )

        if event.event_id in events:
            raise EvidenceCacheError(
                f"Event {event.event_id} appears more than once."
            )

        evidence = event_evidence_for(
            event_id=event.event_id,
            project_id=event.project_id,
            text=event.text,
            reported_activity_id=event.reported_activity_id,
            truth_activity_id=event.truth_activity_id,
            activities=activities[event.project_id],
        )
        _check_event(evidence, activities[event.project_id])
        events[event.event_id] = evidence

    return EvidenceCache(
        split=dataset.split,
        events=MappingProxyType(events),
        activities=MappingProxyType(activities),
    )


def evidence_for(cache: EvidenceCache, event_id: UUID) -> EventEvidence:
    try:
        return cache.events[event_id]
    except KeyError:
        raise EvidenceCacheError(
            f"No cached evidence for event {event_id}."
        ) from None


def components_for(
    cache: EvidenceCache,
    event_id: UUID,
) -> tuple[tuple[CandidateActivity, ...], list[float], list[float]]:
    """
    The activities and their component scores, aligned by identifier.

    Alignment is built by looking each activity's evidence up by its own
    identifier, never by trusting a stored position.
    """
    evidence = evidence_for(cache, event_id)

    try:
        activities = cache.activities[evidence.project_id]
    except KeyError:
        raise EvidenceCacheError(
            f"No activities cached for project {evidence.project_id}."
        ) from None

    if not evidence.text_usable:
        return activities, [], []

    fuzzy: list[float] = []
    tfidf: list[float] = []

    for activity in activities:
        pair = evidence.pairs.get(activity.activity_id)

        if pair is None:
            raise EvidenceCacheError(
                f"No cached evidence for event {event_id} against activity "
                f"{activity.activity_id}."
            )

        fuzzy.append(pair.fuzzy_score)
        tfidf.append(pair.tfidf_score)

    return activities, fuzzy, tfidf


def result_for(
    cache: EvidenceCache,
    event_id: UUID,
    config: MatcherConfig | None = None,
) -> ScoringResult:
    """
    Assemble one configuration's result from cached evidence.

    Built fresh every time: the combined score, the ordering, the margin
    and the pruning all depend on the configuration, so none of them is
    cached.
    """
    config = config or current_matcher_config()
    evidence = evidence_for(cache, event_id)
    activities, fuzzy, tfidf = components_for(cache, event_id)

    named_activity, identifier_supplied = resolve_named_activity(
        list(activities), evidence.reported_activity_id
    )

    return assemble_result(
        evidence.matching_text,
        list(activities),
        fuzzy,
        tfidf,
        named_activity=named_activity,
        identifier_supplied=identifier_supplied,
        config=config,
    )


def production_result_for(
    dataset: SyntheticDataset,
    event: SyntheticEvent,
    config: MatcherConfig | None = None,
) -> ScoringResult:
    """
    The same event scored through the production entry point.

    Used by the equivalence tests, which compare complete results rather
    than scores alone.
    """
    from app.matching import score_candidates

    activities = project_candidates(
        next(
            project.activities
            for project in dataset.projects
            if project.project_id == event.project_id
        )
    )

    return score_candidates(
        event.text,
        list(activities),
        event.reported_activity_id,
        config,
    )


def benchmark_evidence(
    dataset: SyntheticDataset | None = None,
) -> EvidenceBenchmark:
    """
    Measure precomputation on its own, apart from any grid evaluation.

    Generation is timed separately, because it is not what the cache is
    meant to make cheap.
    """
    generation_seconds = 0.0

    if dataset is None:
        start = time.perf_counter()
        dataset = development_dataset()
        generation_seconds = time.perf_counter() - start

    # Timed without tracemalloc: its instrumentation inflates the figure
    # several times over. Memory is measured in a second pass.
    start = time.perf_counter()
    cache = build_evidence_cache(dataset)
    precompute_seconds = time.perf_counter() - start

    tracemalloc.start()
    measured = build_evidence_cache(dataset)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    if measured.pair_count() != cache.pair_count():
        raise EvidenceCacheError(
            "Rebuilding the cache produced a different pair count."
        )

    return EvidenceBenchmark(
        events=len(cache.events),
        pairs=cache.pair_count(),
        generation_seconds=generation_seconds,
        precompute_seconds=precompute_seconds,
        cache_bytes=peak,
    )


def development_cache() -> tuple[SyntheticDataset, EvidenceCache]:
    """The development dataset with its evidence precomputed."""
    dataset = development_dataset()
    return dataset, build_evidence_cache(dataset)
