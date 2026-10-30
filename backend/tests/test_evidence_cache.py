"""
Tests for the cached matching evidence.

The load-bearing claim is equivalence: a result assembled from cached
evidence must equal the result the production entry point produces, over
the complete ScoringResult rather than its scores alone.

The default suite checks every development event under the provisional
configuration. The 420-configuration matrix is marked `slow`.
"""

import dataclasses
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest

from app.matching import (
    IDENTIFIER_AGREES,
    IDENTIFIER_CONFLICT,
    IDENTIFIER_MATCHED_WITHOUT_TEXT,
    IDENTIFIER_NOT_FOUND,
    CandidateActivity,
    score_candidates,
)
from app.matching_config import current_matcher_config
from evaluation.evidence import (
    EvidenceCache,
    EvidenceCacheError,
    PairEvidence,
    benchmark_evidence,
    build_evidence_cache,
    components_for,
    development_cache,
    event_evidence_for,
    evidence_for,
    production_result_for,
    project_candidates,
    result_for,
    to_candidate,
)
from evaluation.synthetic_dataset import (
    CROSS_PROJECT,
    IDENTIFIER_CORRECT,
    IDENTIFIER_INCORRECT_EXISTING,
    IDENTIFIER_NONEXISTENT,
    TEXTLESS,
)

CONFIG = current_matcher_config()

GRID = [
    dataclasses.replace(
        CONFIG,
        version="grid",
        fuzzy_weight=fuzzy,
        tfidf_weight=round(1 - fuzzy, 2),
        accept_floor=accept,
        margin=margin,
        candidate_floor=floor,
    )
    for fuzzy in (0.30, 0.40, 0.50, 0.60, 0.70)
    for accept in (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
    for margin in (0.05, 0.10, 0.15, 0.20)
    for floor in (0.10, 0.15, 0.20)
]


@pytest.fixture(scope="module")
def cached():
    return development_cache()


# -------------------------------------------------------- cache structure


def test_cache_covers_every_event_and_project(cached):
    dataset, cache = cached

    assert cache.split == dataset.split
    assert set(cache.events) == {e.event_id for e in dataset.events}
    assert set(cache.activities) == {
        p.project_id for p in dataset.projects
    }
    assert len(cache.events) == 600


def test_pairs_are_keyed_by_activity_identifier(cached):
    _dataset, cache = cached

    for evidence in cache.events.values():
        for activity_id, pair in evidence.pairs.items():
            assert pair.activity_id == activity_id
            assert pair.event_id == evidence.event_id


def test_pair_count_matches_events_with_usable_text(cached):
    dataset, cache = cached
    usable = [e for e in dataset.events if not e.textless]

    assert cache.pair_count() == sum(
        len(cache.activities[e.project_id]) for e in usable
    )
    assert cache.pair_count() == 11920


def test_cached_structures_are_immutable(cached):
    _dataset, cache = cached

    with pytest.raises(TypeError):
        cache.events[uuid4()] = None

    with pytest.raises(TypeError):
        next(iter(cache.events.values())).pairs[uuid4()] = None

    with pytest.raises(dataclasses.FrozenInstanceError):
        next(iter(cache.events.values())).text_usable = False


def test_activities_are_sorted_for_assembly(cached):
    _dataset, cache = cached

    for activities in cache.activities.values():
        keys = [
            (a.external_id, str(a.activity_id)) for a in activities
        ]
        assert keys == sorted(keys)


# ------------------------------------------------------ project boundaries


def test_no_pair_references_another_project(cached):
    _dataset, cache = cached

    for evidence in cache.events.values():
        owned = {
            a.activity_id for a in cache.activities[evidence.project_id]
        }
        assert set(evidence.pairs) <= owned


def test_every_usable_event_has_evidence_for_its_whole_project(cached):
    _dataset, cache = cached

    for evidence in cache.events.values():
        if not evidence.text_usable:
            continue

        owned = {
            a.activity_id for a in cache.activities[evidence.project_id]
        }
        assert set(evidence.pairs) == owned


def test_foreign_evidence_is_rejected(cached):
    dataset, cache = cached
    event = next(e for e in dataset.events if not e.textless)
    evidence = cache.events[event.event_id]

    foreign_id = next(
        a.activity_id
        for project in dataset.projects
        if project.project_id != event.project_id
        for a in project.activities
    )
    pairs = dict(evidence.pairs)
    pairs[foreign_id] = PairEvidence(event.event_id, foreign_id, 0.5, 0.5)

    tampered = dataclasses.replace(
        dataset,
        events=(
            dataclasses.replace(event, text=event.text),
        ),
        projects=dataset.projects,
    )

    # Rebuild through the checker directly, with the foreign pair present.
    from evaluation.evidence import _check_event

    with pytest.raises(EvidenceCacheError, match="outside its project"):
        _check_event(
            dataclasses.replace(
                evidence, pairs=MappingProxyType(pairs)
            ),
            cache.activities[event.project_id],
        )

    assert tampered.split == dataset.split


def test_missing_evidence_is_rejected(cached):
    dataset, cache = cached
    event = next(e for e in dataset.events if not e.textless)
    evidence = cache.events[event.event_id]

    pairs = dict(evidence.pairs)
    pairs.pop(next(iter(pairs)))

    from evaluation.evidence import _check_event

    with pytest.raises(EvidenceCacheError, match="missing evidence"):
        _check_event(
            dataclasses.replace(evidence, pairs=MappingProxyType(pairs)),
            cache.activities[event.project_id],
        )


def test_unknown_event_lookup_is_rejected(cached):
    _dataset, cache = cached

    with pytest.raises(EvidenceCacheError, match="No cached evidence"):
        evidence_for(cache, uuid4())

    with pytest.raises(EvidenceCacheError, match="No cached evidence"):
        components_for(cache, uuid4())


def test_duplicate_events_are_rejected(cached):
    dataset, _cache = cached
    duplicated = dataclasses.replace(
        dataset, events=dataset.events + (dataset.events[0],)
    )

    with pytest.raises(EvidenceCacheError, match="more than once"):
        build_evidence_cache(duplicated)


def test_unknown_project_is_rejected(cached):
    dataset, _cache = cached
    orphan = dataclasses.replace(
        dataset.events[0], project_id=UUID(int=12345)
    )
    broken = dataclasses.replace(dataset, events=(orphan,))

    with pytest.raises(EvidenceCacheError, match="unknown project"):
        build_evidence_cache(broken)


# ---------------------------------------------------- textless semantics


def test_textless_events_carry_no_pairs(cached):
    dataset, cache = cached
    textless = [e for e in dataset.events if e.kind == TEXTLESS]

    assert len(textless) == 4

    for event in textless:
        evidence = cache.events[event.event_id]

        assert evidence.text_usable is False
        assert evidence.matching_text == ""
        # No fabricated zeros: absence of a pair is not a score of zero.
        assert dict(evidence.pairs) == {}


def test_textless_components_are_empty_not_zero(cached):
    dataset, cache = cached
    event = next(e for e in dataset.events if e.kind == TEXTLESS)

    activities, fuzzy, tfidf = components_for(cache, event.event_id)

    assert activities
    assert fuzzy == []
    assert tfidf == []


def test_a_textless_event_with_pairs_is_rejected(cached):
    dataset, cache = cached
    event = next(e for e in dataset.events if e.kind == TEXTLESS)
    evidence = cache.events[event.event_id]

    from evaluation.evidence import _check_event

    invented = PairEvidence(
        event.event_id,
        cache.activities[event.project_id][0].activity_id,
        0.0,
        0.0,
    )

    with pytest.raises(EvidenceCacheError, match="must carry none"):
        _check_event(
            dataclasses.replace(
                evidence, pairs=MappingProxyType({invented.activity_id: invented})
            ),
            cache.activities[event.project_id],
        )


# ------------------------------------------------- identifier separation


def test_identifier_evidence_is_held_apart_from_scores(cached):
    dataset, cache = cached
    named = next(
        e for e in dataset.events
        if e.identifier_kind == IDENTIFIER_CORRECT
    )

    evidence = cache.events[named.event_id]

    assert evidence.reported_activity_id == named.reported_activity_id
    # A pair carries only the two component scores.
    fields = {f.name for f in dataclasses.fields(PairEvidence)}
    assert fields == {
        "event_id",
        "activity_id",
        "fuzzy_score",
        "tfidf_score",
    }


def test_identifier_states_are_reached_from_cached_evidence(cached):
    dataset, cache = cached
    states = set()

    for event in dataset.events:
        states.add(result_for(cache, event.event_id, CONFIG).identifier_state)

    assert IDENTIFIER_AGREES in states
    assert IDENTIFIER_CONFLICT in states
    assert IDENTIFIER_NOT_FOUND in states


def test_dataset_supplies_the_identifier_kinds_under_test(cached):
    dataset, _cache = cached
    kinds = {e.identifier_kind for e in dataset.events}

    assert IDENTIFIER_CORRECT in kinds
    assert IDENTIFIER_INCORRECT_EXISTING in kinds
    assert IDENTIFIER_NONEXISTENT in kinds


# ------------------------------------------- nothing configuration-cached


def test_no_configuration_dependent_value_is_cached(cached):
    _dataset, cache = cached

    cached_fields = {f.name for f in dataclasses.fields(cache)}
    assert cached_fields == {"split", "events", "activities"}

    from evaluation.evidence import EventEvidence

    event_fields = {f.name for f in dataclasses.fields(EventEvidence)}
    for forbidden in (
        "combined_score",
        "rank",
        "margin",
        "top_score",
        "stored",
        "scored",
        "result",
    ):
        assert forbidden not in event_fields


def test_results_differ_by_configuration_from_one_cache(cached):
    dataset, cache = cached
    event = next(
        e for e in dataset.events
        if not e.textless and e.truth_activity_id is not None
    )

    light = dataclasses.replace(
        CONFIG, version="light", fuzzy_weight=0.30, tfidf_weight=0.70
    )
    heavy = dataclasses.replace(
        CONFIG, version="heavy", fuzzy_weight=0.70, tfidf_weight=0.30
    )

    first = result_for(cache, event.event_id, light)
    second = result_for(cache, event.event_id, heavy)

    # Components identical, combined scores not.
    assert {c.activity.activity_id: c.fuzzy_score for c in first.scored} == {
        c.activity.activity_id: c.fuzzy_score for c in second.scored
    }
    assert first != second


def test_a_fresh_result_is_returned_each_call(cached):
    dataset, cache = cached
    event_id = dataset.events[0].event_id

    first = result_for(cache, event_id, CONFIG)
    second = result_for(cache, event_id, CONFIG)

    assert first == second
    assert first is not second


# ---------------------------------------------------------- equivalence


def test_every_development_event_matches_production(cached):
    """
    The default equivalence pass: all 600 events, provisional config.

    Compared over the complete ScoringResult, not the scores alone.
    """
    dataset, cache = cached
    compared = 0

    for event in dataset.events:
        assert result_for(cache, event.event_id, CONFIG) == (
            production_result_for(dataset, event, CONFIG)
        ), event.event_id
        compared += 1

    assert compared == 600


def edge_cases():
    """Hand-built cases the dataset cannot be relied on to contain."""

    def activity(external_id, name, index):
        return CandidateActivity(UUID(int=index), external_id, name)

    roof = activity("A-110", "install roof membrane bay one", 1)
    slab = activity("A-101", "pour foundation slab level two", 2)
    glazing = activity("A-120", "install window glazing zone north", 3)
    tie_low = activity("T-001", "paint handrail", 11)
    tie_high = activity("T-002", "paint handrail", 22)

    schedule = (roof, slab, glazing)

    # Eight candidates, so the stored limit of five prunes the tail.
    bays = tuple(
        activity(f"M-{index:03d}", f"install roof membrane bay {index}", 100 + index)
        for index in range(8)
    )
    # Scores far below the floor and outside the limit, but named by the
    # report, so it must be retained anyway.
    outsider = activity("ZZ-900", "catering invoice reconciled", 900)

    return [
        ("multiple candidates", "install roof membrane bay one", schedule, None),
        ("agreement", "install roof membrane bay one", schedule, "A-110"),
        ("conflict", "install roof membrane bay one", schedule, "A-101"),
        ("not found", "install roof membrane bay one", schedule, "Z-999"),
        ("pruning", "install roof membrane bay one", schedule, None),
        ("named retained", "install roof membrane bay one", schedule, "A-101"),
        ("sole candidate", "install roof membrane bay one", (roof,), None),
        ("no candidates", "install roof membrane bay one", (), None),
        ("no candidates, named", "install roof membrane bay one", (), "A-110"),
        ("tie", "paint handrail", (tie_high, tie_low), None),
        ("tie with identifier", "paint handrail", (tie_high, tie_low), "T-002"),
        ("textless", "   ", schedule, None),
        ("textless named", "!!!", schedule, "A-120"),
        ("textless not found", "", schedule, "Z-999"),
        ("over stored limit", "install roof membrane bay three", bays, None),
        (
            "named retained beyond limit",
            "install roof membrane bay three",
            bays + (outsider,),
            "ZZ-900",
        ),
    ]


def assembled_and_direct(text, activities, reported, config):
    ordered = tuple(sorted(activities, key=lambda a: (a.external_id, str(a.activity_id))))
    event_id = uuid4()
    project_id = uuid4()

    evidence = event_evidence_for(
        event_id=event_id,
        project_id=project_id,
        text=text,
        reported_activity_id=reported,
        truth_activity_id=None,
        activities=ordered,
    )
    cache = EvidenceCache(
        split="edge",
        events=MappingProxyType({event_id: evidence}),
        activities=MappingProxyType({project_id: ordered}),
    )

    return (
        result_for(cache, event_id, config),
        score_candidates(text, list(activities), reported, config),
    )


def test_edge_cases_match_production_under_the_provisional_config():
    for label, text, activities, reported in edge_cases():
        assembled, direct = assembled_and_direct(
            text, activities, reported, CONFIG
        )
        assert assembled == direct, label


def test_edge_cases_reach_the_states_they_claim_to():
    """Guards the coverage this suite is relied on for."""
    states = set()
    seen = {
        "sole": False,
        "none": False,
        "tie": False,
        "pruned": False,
        "named_retained": False,
        "textless": False,
    }

    for label, text, activities, reported in edge_cases():
        assembled, _direct = assembled_and_direct(
            text, activities, reported, CONFIG
        )
        states.add(assembled.identifier_state)

        if len(assembled.scored) == 1:
            seen["sole"] = True
        if not activities:
            seen["none"] = True
        scores = [c.combined_score for c in assembled.scored]
        if scores and len(scores) != len(set(scores)):
            seen["tie"] = True
        if len(assembled.stored) < len(assembled.scored):
            seen["pruned"] = True
        if not assembled.text_usable:
            seen["textless"] = True

        named = [c for c in assembled.stored if c.identifier_match]
        if named and named[0].scored_position is not None:
            below = named[0].combined_score < CONFIG.candidate_floor
            outside = named[0].scored_position > CONFIG.stored_candidates
            if below or outside:
                seen["named_retained"] = True

    assert IDENTIFIER_AGREES in states
    assert IDENTIFIER_CONFLICT in states
    assert IDENTIFIER_NOT_FOUND in states
    assert IDENTIFIER_MATCHED_WITHOUT_TEXT in states
    assert [name for name, found in seen.items() if not found] == []


@pytest.mark.slow
def test_edge_cases_match_production_across_the_whole_grid():
    assert len(GRID) == 420
    compared = 0

    for config in GRID:
        for label, text, activities, reported in edge_cases():
            assembled, direct = assembled_and_direct(
                text, activities, reported, config
            )
            assert assembled == direct, (label, config.fuzzy_weight)
            compared += 1

    assert compared == 420 * len(edge_cases())


@pytest.mark.slow
def test_a_sample_of_events_matches_production_across_the_whole_grid(cached):
    """Twenty events, chosen deterministically, against all 420 cells."""
    dataset, cache = cached
    sample = dataset.events[::30][:20]

    assert len(sample) == 20

    compared = 0

    for config in GRID:
        for event in sample:
            assert result_for(cache, event.event_id, config) == (
                production_result_for(dataset, event, config)
            ), (event.event_id, config.fuzzy_weight)
            compared += 1

    assert compared == 420 * 20


# ------------------------------------------------------------- benchmark


def test_benchmark_reports_time_and_memory(cached):
    dataset, _cache = cached

    measured = benchmark_evidence(dataset)

    assert measured.events == 600
    assert measured.pairs == 11920
    # Generation is not timed when a dataset is supplied.
    assert measured.generation_seconds == 0.0
    assert measured.precompute_seconds > 0.0
    assert measured.cache_bytes > 0


def test_conversion_preserves_identity_and_text(cached):
    dataset, _cache = cached
    activity = dataset.projects[0].activities[0]

    candidate = to_candidate(activity)

    assert candidate.activity_id == activity.activity_id
    assert candidate.external_id == activity.external_id
    assert candidate.name == activity.name
    assert candidate.description is None


def test_project_candidates_are_sorted_and_complete(cached):
    dataset, _cache = cached
    project = dataset.projects[0]

    candidates = project_candidates(project.activities)

    assert len(candidates) == len(project.activities)
    assert [c.external_id for c in candidates] == sorted(
        a.external_id for a in project.activities
    )


def test_cross_project_events_are_scored_against_their_own_project(cached):
    """A borrowed text is matched against the project it was assigned to."""
    dataset, cache = cached
    event = next(e for e in dataset.events if e.kind == CROSS_PROJECT)

    activities, _fuzzy, _tfidf = components_for(cache, event.event_id)
    owned = {a.activity_id for a in cache.activities[event.project_id]}

    assert {a.activity_id for a in activities} == owned
    assert event.source_activity_id not in owned
