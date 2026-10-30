"""
Tests for the synthetic dataset generator.

No held-out data is generated here. The held-out split must not exist
before a development configuration qualifies and is frozen, so partition
disjointness is tested at the vocabulary level instead.
"""

import dataclasses
import io
from math import floor

import pytest

from app.matching import matching_text
from evaluation.synthetic_dataset import (
    ACTIONS,
    CROSS_PROJECT,
    DEVELOPMENT,
    DEVELOPMENT_SEED,
    ELEMENTS,
    FILLER_PHRASES,
    HELD_OUT,
    HELD_OUT_SEED,
    IDENTIFIER_CORRECT,
    IDENTIFIER_INCORRECT_EXISTING,
    IDENTIFIER_NONE,
    IDENTIFIER_NONEXISTENT,
    MIN_ACTIVITIES,
    MIN_EVENTS,
    MIN_NEGATIVES,
    MIN_POSITIVES,
    MIN_PROJECTS,
    OFF_SCHEDULE,
    OFF_SCHEDULE_PHRASES,
    PARTITION_A,
    PARTITION_B,
    POSITIVE,
    QUALIFIERS,
    TEXTLESS,
    TRANSFORMATIONS,
    DatasetValidationError,
    SyntheticDataset,
    generate_dataset,
    partition_of,
    textless_count,
    validate_dataset,
)


@pytest.fixture(scope="module")
def dataset() -> SyntheticDataset:
    built = generate_dataset(DEVELOPMENT)
    validate_dataset(built)
    return built


# ------------------------------------------------------------ determinism


def test_same_seed_produces_an_identical_dataset():
    assert generate_dataset(DEVELOPMENT) == generate_dataset(DEVELOPMENT)


def test_identifiers_are_derived_not_random(dataset):
    """uuid5, so a rerun reproduces the same identifiers."""
    again = generate_dataset(DEVELOPMENT)

    assert [p.project_id for p in dataset.projects] == [
        p.project_id for p in again.projects
    ]
    assert [e.event_id for e in dataset.events] == [
        e.event_id for e in again.events
    ]


def generator_source() -> str:
    """The generator's source, located from the module rather than the CWD."""
    from pathlib import Path

    from evaluation import synthetic_dataset

    return Path(synthetic_dataset.__file__).read_text(encoding="utf-8")


def imported_names():
    """
    Every module and name the generator imports, read from its syntax tree.

    Parsed rather than grepped, so a word in a docstring cannot pass or
    fail the check.
    """
    import ast

    tree = ast.parse(generator_source())

    modules: set[str] = set()
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            for alias in node.names:
                names.add(alias.name)

    return modules, names


def test_generator_uses_no_module_level_randomness():
    modules, names = imported_names()

    assert "uuid4" not in names
    assert "random" not in modules
    assert "secrets" not in modules
    assert "default_rng" not in names
    # One explicit generator, built from the split's own seed.
    assert {"Generator", "PCG64"} <= names
    assert "uuid5" in names

    assert "Generator(PCG64(seed))" in generator_source()


def test_the_generator_does_not_import_production_matching_logic():
    """
    Validation may borrow the normalizer; nothing else may be imported.

    Importing the scorer, the router or the matcher configuration would
    couple the dataset to the thing it is meant to measure.
    """
    modules, names = imported_names()

    assert "app.matching" in modules
    assert "matching_text" in names

    for forbidden in (
        "score_candidates",
        "assemble_result",
        "route",
        "current_matcher_config",
    ):
        assert forbidden not in names, forbidden

    for forbidden in (
        "app.matching_routing",
        "app.matching_config",
        "app.matching_explanations",
    ):
        assert forbidden not in modules, forbidden

    # The only production import is the normalizer.
    assert {m for m in modules if m.startswith("app.")} == {"app.matching"}


# ----------------------------------------------------- split and seeds


def test_development_uses_its_own_seed_and_partition(dataset):
    assert dataset.split == DEVELOPMENT
    assert dataset.seed == DEVELOPMENT_SEED == 20261101
    assert dataset.partition == PARTITION_A


def test_held_out_is_configured_but_not_generated_here():
    """The seeds differ; generating the held-out split is not done yet."""
    assert HELD_OUT_SEED == 20261102
    assert HELD_OUT_SEED != DEVELOPMENT_SEED
    assert HELD_OUT != DEVELOPMENT


def test_vocabulary_partitions_are_disjoint():
    """
    Checked at the vocabulary level, without generating the held-out split.

    Disjoint element lists are what prevent an activity name from occurring
    in both splits, whatever generation path produced it.
    """
    for values in (ACTIONS, ELEMENTS, QUALIFIERS, OFF_SCHEDULE_PHRASES,
                   FILLER_PHRASES):
        first = set(partition_of(values, PARTITION_A))
        second = set(partition_of(values, PARTITION_B))

        assert first & second == set()
        assert first | second == set(values)
        assert first and second


def test_unknown_partition_is_rejected():
    with pytest.raises(ValueError, match="Unknown vocabulary partition"):
        partition_of(ELEMENTS, "C")


def test_unknown_split_is_rejected():
    with pytest.raises(ValueError, match="Unknown split"):
        generate_dataset("staging")


def test_every_activity_name_uses_only_this_partition(dataset):
    allowed = set(partition_of(ELEMENTS, PARTITION_A))
    excluded = set(partition_of(ELEMENTS, PARTITION_B))

    for activity in dataset.activities():
        assert any(element in activity.name for element in allowed)
        assert not any(element in activity.name for element in excluded)


# ------------------------------------------------------- size and counts


def test_counts_meet_the_contract_minimums(dataset):
    counts = dataset.counts

    assert counts.projects >= MIN_PROJECTS
    assert counts.activities >= MIN_ACTIVITIES
    assert counts.positives >= MIN_POSITIVES
    assert counts.negatives >= MIN_NEGATIVES
    assert counts.events >= MIN_EVENTS


def test_counts_are_realized_not_assumed(dataset):
    """Counts come from the emitted events, not from the percentages."""
    counts = dataset.counts
    events = dataset.events

    assert counts.events == len(events)
    assert counts.positives == sum(
        1 for e in events if e.truth_activity_id is not None
    )
    assert counts.negatives == sum(
        1 for e in events if e.truth_activity_id is None
    )
    assert counts.positives + counts.negatives == counts.events


def test_identifier_counts_are_reported_per_class(dataset):
    """A combined count would hide an odd distribution within one class."""
    counts = dataset.counts

    assert set(counts.positive_identifier_kinds) <= {
        IDENTIFIER_CORRECT,
        IDENTIFIER_INCORRECT_EXISTING,
        IDENTIFIER_NONE,
    }
    assert set(counts.negative_identifier_kinds) <= {
        IDENTIFIER_NONEXISTENT,
        IDENTIFIER_NONE,
    }

    assert sum(counts.positive_identifier_kinds.values()) == counts.positives
    assert sum(counts.negative_identifier_kinds.values()) == counts.negatives

    # Draws, so realized counts sit near the nominal shares, not on them.
    assert 0.15 < counts.positive_identifier_kinds[IDENTIFIER_CORRECT] / (
        counts.positives
    ) < 0.35


def test_event_kinds_are_tallied(dataset):
    kinds = dataset.counts.event_kinds

    assert kinds[POSITIVE] == dataset.counts.positives
    assert kinds[TEXTLESS] == dataset.counts.textless_negatives
    assert OFF_SCHEDULE in kinds
    assert CROSS_PROJECT in kinds


# ------------------------------------------------------- textless share


def test_textless_count_rounds_half_up_not_to_even():
    """Python's round() would send 0.5 to zero here."""
    assert textless_count(25) == 1
    assert round(0.02 * 25) == 0

    assert textless_count(200) == 4
    assert textless_count(0) == 0
    assert textless_count(50) == 1

    for negatives in range(0, 500):
        assert textless_count(negatives) == floor(
            0.02 * negatives + 0.5
        )


def test_textless_events_are_negative_and_have_no_usable_text(dataset):
    textless = [e for e in dataset.events if e.textless]

    assert len(textless) == textless_count(dataset.counts.negatives) == 4

    for event in textless:
        assert event.kind == TEXTLESS
        assert event.truth_activity_id is None
        assert matching_text(event.text) == ""


# ---------------------------------------------------------- truth policy


def test_positives_resolve_to_an_activity_in_their_own_project(dataset):
    by_project = dataset.activities_by_project()

    for event in dataset.events:
        if event.truth_activity_id is None:
            continue

        owned = {a.activity_id for a in by_project[event.project_id]}
        assert event.truth_activity_id in owned


def test_cross_project_events_are_negatives_with_provenance(dataset):
    crossed = [e for e in dataset.events if e.kind == CROSS_PROJECT]
    by_project = dataset.activities_by_project()

    assert crossed

    for event in crossed:
        # The source activity exists, but not in this event's project, so
        # it is never a valid match and the truth stays None.
        assert event.truth_activity_id is None
        assert event.source_activity_id is not None

        owned = {a.activity_id for a in by_project[event.project_id]}
        assert event.source_activity_id not in owned


def test_off_schedule_and_textless_events_carry_no_source(dataset):
    for event in dataset.events:
        if event.kind in (OFF_SCHEDULE, TEXTLESS):
            assert event.source_activity_id is None


def test_transformations_are_shared_by_both_partitions(dataset):
    used = {
        e.transformation
        for e in dataset.events
        if e.kind in (POSITIVE, CROSS_PROJECT)
    }

    assert used == set(TRANSFORMATIONS)


# ------------------------------------------------------ identifier rules


def test_correct_identifiers_name_the_truth_activity(dataset):
    activities = {a.activity_id: a for a in dataset.activities()}

    for event in dataset.events:
        if event.identifier_kind != IDENTIFIER_CORRECT:
            continue

        truth = activities[event.truth_activity_id]
        assert event.reported_activity_id == truth.external_id


def test_incorrect_identifiers_exist_but_are_not_the_truth(dataset):
    activities = {a.activity_id: a for a in dataset.activities()}
    by_project = dataset.activities_by_project()

    for event in dataset.events:
        if event.identifier_kind != IDENTIFIER_INCORRECT_EXISTING:
            continue

        owned = {a.external_id for a in by_project[event.project_id]}
        truth = activities[event.truth_activity_id]

        assert event.reported_activity_id in owned
        assert event.reported_activity_id != truth.external_id


def test_nonexistent_identifiers_match_no_activity(dataset):
    known = {a.external_id for a in dataset.activities()}

    for event in dataset.events:
        if event.identifier_kind != IDENTIFIER_NONEXISTENT:
            continue

        assert event.reported_activity_id not in known


def test_events_without_an_identifier_report_none(dataset):
    for event in dataset.events:
        if event.identifier_kind == IDENTIFIER_NONE:
            assert event.reported_activity_id is None
        else:
            assert event.reported_activity_id is not None


def test_negatives_never_name_an_existing_activity(dataset):
    """The reason identifier_matched_without_text is unreachable here."""
    for event in dataset.events:
        if event.truth_activity_id is None:
            assert event.identifier_kind in (
                IDENTIFIER_NONEXISTENT,
                IDENTIFIER_NONE,
            )


# ------------------------------------------------------------- name rules


def test_normalized_activity_names_are_unique(dataset):
    normalized = [matching_text(a.name) for a in dataset.activities()]

    assert len(set(normalized)) == len(normalized)


def test_no_normalized_name_appears_in_two_projects(dataset):
    owners: dict[str, set] = {}

    for activity in dataset.activities():
        owners.setdefault(matching_text(activity.name), set()).add(
            activity.project_id
        )

    shared = {name for name, found in owners.items() if len(found) > 1}
    assert shared == set()


def test_identifiers_are_unique(dataset):
    activities = dataset.activities()

    assert len({a.activity_id for a in activities}) == len(activities)
    assert len({p.project_id for p in dataset.projects}) == len(
        dataset.projects
    )
    assert len({e.event_id for e in dataset.events}) == len(dataset.events)


# ---------------------------------------------------------- validation


def tampered(dataset, index, **changes):
    events = list(dataset.events)
    events[index] = dataclasses.replace(events[index], **changes)
    return dataclasses.replace(dataset, events=tuple(events))


def first_index(dataset, predicate):
    return next(
        index for index, e in enumerate(dataset.events) if predicate(e)
    )


def test_validation_accepts_the_generated_dataset(dataset):
    validate_dataset(dataset)


def test_validation_rejects_a_positive_resolving_outside_its_project(dataset):
    foreign = dataset.projects[1].activities[0].activity_id
    index = first_index(dataset, lambda e: e.project_id == (
        dataset.projects[0].project_id
    ) and e.truth_activity_id is not None)

    broken = tampered(dataset, index, truth_activity_id=foreign)

    with pytest.raises(DatasetValidationError, match="outside its project"):
        validate_dataset(broken)


def test_validation_rejects_a_cross_project_event_given_a_truth(dataset):
    index = first_index(dataset, lambda e: e.kind == CROSS_PROJECT)
    event = dataset.events[index]
    owned = next(
        p for p in dataset.projects if p.project_id == event.project_id
    ).activities[0]

    broken = tampered(dataset, index, truth_activity_id=owned.activity_id)

    with pytest.raises(DatasetValidationError):
        validate_dataset(broken)


def test_validation_rejects_a_cross_project_event_without_provenance(dataset):
    index = first_index(dataset, lambda e: e.kind == CROSS_PROJECT)

    broken = tampered(dataset, index, source_activity_id=None)

    with pytest.raises(DatasetValidationError, match="lost provenance"):
        validate_dataset(broken)


def test_validation_rejects_a_textless_event_with_usable_text(dataset):
    index = first_index(dataset, lambda e: e.textless)

    broken = tampered(dataset, index, text="install roof membrane bay one")

    with pytest.raises(DatasetValidationError, match="usable text"):
        validate_dataset(broken)


def test_validation_rejects_a_mislabelled_textless_flag(dataset):
    index = first_index(dataset, lambda e: e.kind == OFF_SCHEDULE)

    broken = tampered(dataset, index, textless=True)

    with pytest.raises(DatasetValidationError, match="mislabels"):
        validate_dataset(broken)


def test_validation_rejects_a_nonexistent_identifier_that_exists(dataset):
    index = first_index(
        dataset, lambda e: e.identifier_kind == IDENTIFIER_NONEXISTENT
    )
    real = dataset.activities()[0].external_id

    broken = tampered(dataset, index, reported_activity_id=real)

    with pytest.raises(DatasetValidationError, match="activity that exists"):
        validate_dataset(broken)


def test_validation_rejects_a_correct_identifier_that_is_wrong(dataset):
    index = first_index(
        dataset, lambda e: e.identifier_kind == IDENTIFIER_CORRECT
    )

    broken = tampered(dataset, index, reported_activity_id="A00-999")

    with pytest.raises(DatasetValidationError, match="correct id"):
        validate_dataset(broken)


def test_validation_rejects_an_unexpected_reported_identifier(dataset):
    index = first_index(
        dataset, lambda e: e.identifier_kind == IDENTIFIER_NONE
    )

    broken = tampered(dataset, index, reported_activity_id="A00-000")

    with pytest.raises(DatasetValidationError, match="unexpected id"):
        validate_dataset(broken)


def test_validation_rejects_an_event_naming_no_known_project(dataset):
    from uuid import UUID

    index = 0
    broken = tampered(dataset, index, project_id=UUID(int=999))

    with pytest.raises(DatasetValidationError, match="no known project"):
        validate_dataset(broken)


def test_validation_rejects_a_dataset_below_the_minimum_sizes(dataset):
    small = generate_dataset(
        DEVELOPMENT,
        projects=2,
        activities_per_project=5,
        positives_per_project=5,
        negatives_per_project=2,
    )

    with pytest.raises(DatasetValidationError, match="below the minimum"):
        validate_dataset(small)


def test_validation_rejects_a_wrong_textless_share(dataset):
    index = first_index(dataset, lambda e: e.kind == TEXTLESS)
    events = list(dataset.events)
    events[index] = dataclasses.replace(
        events[index], kind=OFF_SCHEDULE, textless=False, text="permit renewed"
    )
    rebuilt = dataclasses.replace(dataset, events=tuple(events))

    with pytest.raises(DatasetValidationError, match="textless negatives"):
        validate_dataset(rebuilt)
