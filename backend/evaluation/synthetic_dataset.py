"""
Deterministic synthetic datasets for matching evaluation.

The generator is independent of production matching: it imports no scorer,
no router and no matcher configuration. Validation borrows the production
text normalization, because a second normalizer could disagree with the
matcher about whether two activity names are the same.

Determinism comes from one numpy Generator per dataset and identifiers
derived with uuid5. Nothing here uses module-level randomness, and no
identifier is drawn from uuid4.

The vocabulary is partitioned at the vocabulary level rather than filtered
after generation: development draws from partition A, held-out from
partition B, and their element lists are disjoint, so no generation path can
produce an activity name shared between the splits. The six transformation
kinds are algorithms rather than vocabulary and are shared by both splits.

Evaluation coverage limitation
------------------------------
Negative events receive only non-existent identifiers, and every textless
event is a negative, so no generated event is both textless and naming an
eligible activity. The `identifier_matched_without_text` routing reason is
therefore never exercised by evaluation data. Unit tests establish that the
routing condition works; this evaluation does not measure it, and no claim
that evaluation validates every routing path is warranted.
"""

from dataclasses import dataclass
from math import floor
from typing import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from numpy.random import PCG64, Generator

from app.matching import matching_text


DEVELOPMENT = "development"
HELD_OUT = "held_out"

DEVELOPMENT_SEED = 20261101
HELD_OUT_SEED = 20261102

PARTITION_A = "A"
PARTITION_B = "B"

SPLIT_SEEDS: Mapping[str, int] = {
    DEVELOPMENT: DEVELOPMENT_SEED,
    HELD_OUT: HELD_OUT_SEED,
}

SPLIT_PARTITIONS: Mapping[str, str] = {
    DEVELOPMENT: PARTITION_A,
    HELD_OUT: PARTITION_B,
}

#: Namespace for uuid5, so identifiers are reproducible across runs.
ID_NAMESPACE = uuid5(NAMESPACE_URL, "https://planbridge.local/evaluation")

# Contract minimums, per split.
MIN_PROJECTS = 20
MIN_ACTIVITIES = 400
MIN_POSITIVES = 400
MIN_NEGATIVES = 200
MIN_EVENTS = 600

#: Share of negative events generated with no usable text.
TEXTLESS_SHARE = 0.02

POSITIVE = "positive"
OFF_SCHEDULE = "off_schedule"
CROSS_PROJECT = "cross_project"
TEXTLESS = "textless"

IDENTIFIER_CORRECT = "correct"
IDENTIFIER_INCORRECT_EXISTING = "incorrect_existing"
IDENTIFIER_NONEXISTENT = "nonexistent"
IDENTIFIER_NONE = "none"

VERBATIM = "verbatim"
TYPO = "typo"
REORDER = "reorder"
ABBREVIATE = "abbreviate"
PARTIAL = "partial"
FILLER = "filler"
NOT_DERIVED = "not_derived"

#: Shared by both partitions: these are algorithms, not vocabulary.
TRANSFORMATIONS = (VERBATIM, TYPO, REORDER, ABBREVIATE, PARTIAL, FILLER)

# Identifier draw thresholds, cumulative.
POSITIVE_CORRECT_THRESHOLD = 0.25
POSITIVE_INCORRECT_THRESHOLD = 0.35
NEGATIVE_NONEXISTENT_THRESHOLD = 0.15

#: Prefix reserved for identifiers that name nothing.
NONEXISTENT_PREFIX = "ZZ"

ACTIONS = (
    "install", "pour", "erect", "commission", "survey",
    "seal", "inspect", "backfill", "fix", "test",
)

ELEMENTS = (
    "roof membrane", "foundation slab", "window glazing", "handrail",
    "cladding panel", "drainage run", "ductwork riser", "cable tray",
    "floor screed", "balustrade",
    "parapet flashing", "sump pump", "fire damper", "lift guide",
    "curtain wall", "soffit board", "retaining wall", "pile cap",
    "ring beam", "stair flight",
)

QUALIFIERS = (
    "bay one", "bay two", "bay three", "bay four",
    "level one", "level two", "level three", "level four",
    "grid alpha", "grid bravo", "grid charlie", "grid delta",
    "zone north", "zone south", "zone east", "zone west",
    "block red", "block blue", "block green", "block amber",
    "wing upper", "wing lower", "core central", "core service",
)

OFF_SCHEDULE_PHRASES = (
    "catering invoice reconciled", "site permit renewed",
    "insurance certificate filed", "staff induction completed",
    "visitor log archived", "stationery order placed",
    "payroll query resolved", "canteen rota published",
    "parking allocation revised", "training record updated",
    "notice board replaced", "first aid kit restocked",
    "mileage claim approved", "supplier contact changed",
    "badge printer serviced", "waste collection rescheduled",
)

FILLER_PHRASES = (
    "team reports that", "update for the week",
    "noted on site that", "per the daily log",
    "confirmed by phone that", "as discussed",
    "handover note says", "progress call recorded",
    "site diary entry", "foreman confirms",
    "weekly summary states", "verbal update that",
)

#: Each normalizes to the empty string under the production normalizer.
TEXTLESS_TEXTS = ("", "   ", "---", "!!! ???")


class DatasetValidationError(ValueError):
    """Raised when a generated dataset breaks a contract invariant."""


@dataclass(frozen=True)
class SyntheticActivity:
    activity_id: UUID
    project_id: UUID
    external_id: str
    name: str


@dataclass(frozen=True)
class SyntheticProject:
    project_id: UUID
    name: str
    activities: tuple[SyntheticActivity, ...]


@dataclass(frozen=True)
class SyntheticEvent:
    event_id: UUID
    project_id: UUID
    text: str
    reported_activity_id: str | None
    #: Set only when the event describes an activity in its own project.
    #: None marks a negative, and is the single definition of one.
    truth_activity_id: UUID | None
    #: Provenance for a cross-project event: the activity the text came
    #: from, which belongs to a different project and is never a valid
    #: match for this event.
    source_activity_id: UUID | None
    kind: str
    transformation: str
    identifier_kind: str
    textless: bool


@dataclass(frozen=True)
class DatasetCounts:
    projects: int
    activities: int
    events: int
    positives: int
    negatives: int
    textless_negatives: int
    event_kinds: Mapping[str, int]
    #: Realized identifier counts, reported per event class. A combined
    #: count would hide an unexpected distribution within one class.
    positive_identifier_kinds: Mapping[str, int]
    negative_identifier_kinds: Mapping[str, int]


@dataclass(frozen=True)
class SyntheticDataset:
    split: str
    seed: int
    partition: str
    projects: tuple[SyntheticProject, ...]
    events: tuple[SyntheticEvent, ...]
    counts: DatasetCounts

    def activities(self) -> tuple[SyntheticActivity, ...]:
        return tuple(
            activity
            for project in self.projects
            for activity in project.activities
        )

    def activities_by_project(self) -> Mapping[UUID, tuple[SyntheticActivity, ...]]:
        return {
            project.project_id: project.activities
            for project in self.projects
        }


def partition_of(values: tuple[str, ...], partition: str) -> tuple[str, ...]:
    """
    Split a vocabulary list in half at a fixed index.

    The halves are disjoint, which is what keeps activity names from
    appearing in both splits.
    """
    half = len(values) // 2

    if partition == PARTITION_A:
        return values[:half]

    if partition == PARTITION_B:
        return values[half:]

    raise ValueError(f"Unknown vocabulary partition {partition!r}.")


def textless_count(negatives: int) -> int:
    """
    The 2 per cent textless share, rounded half up.

    Python's round() is banker's rounding, which would send some shares to
    an even neighbour. Generation and validation use this one helper.
    """
    return floor(TEXTLESS_SHARE * negatives + 0.5)


def _identifier(*parts: object) -> UUID:
    return uuid5(ID_NAMESPACE, ":".join(str(part) for part in parts))


def _apply_typo(rng: Generator, text: str) -> str:
    words = text.split()
    index = int(rng.integers(0, len(words)))
    word = words[index]

    if len(word) < 4:
        return text

    cut = int(rng.integers(1, len(word) - 1))
    words[index] = word[:cut] + word[cut + 1 :]
    return " ".join(words)


def _apply_reorder(rng: Generator, text: str) -> str:
    words = text.split()
    order = rng.permutation(len(words))
    return " ".join(words[index] for index in order)


def _apply_abbreviate(rng: Generator, text: str) -> str:
    words = text.split()
    index = int(rng.integers(0, len(words)))

    if len(words[index]) > 4:
        words[index] = words[index][:4]

    return " ".join(words)


def _apply_partial(rng: Generator, text: str) -> str:
    words = text.split()

    if len(words) < 3:
        return text

    keep = int(rng.integers(2, len(words)))
    start = int(rng.integers(0, len(words) - keep + 1))
    return " ".join(words[start : start + keep])


def _apply_filler(rng: Generator, text: str, fillers: tuple[str, ...]) -> str:
    phrase = fillers[int(rng.integers(0, len(fillers)))]
    return f"{phrase} {text}"


def _transform(
    rng: Generator,
    text: str,
    kind: str,
    fillers: tuple[str, ...],
) -> str:
    if kind == VERBATIM:
        return text
    if kind == TYPO:
        return _apply_typo(rng, text)
    if kind == REORDER:
        return _apply_reorder(rng, text)
    if kind == ABBREVIATE:
        return _apply_abbreviate(rng, text)
    if kind == PARTIAL:
        return _apply_partial(rng, text)
    if kind == FILLER:
        return _apply_filler(rng, text, fillers)

    raise ValueError(f"Unknown transformation {kind!r}.")


def generate_dataset(
    split: str = DEVELOPMENT,
    projects: int = MIN_PROJECTS,
    activities_per_project: int = MIN_ACTIVITIES // MIN_PROJECTS,
    positives_per_project: int = MIN_POSITIVES // MIN_PROJECTS,
    negatives_per_project: int = MIN_NEGATIVES // MIN_PROJECTS,
) -> SyntheticDataset:
    """
    Generate one split deterministically from its own recorded seed.

    The split determines both the seed and the vocabulary partition, so a
    dataset cannot be generated from one split's seed over the other's
    vocabulary.
    """
    if split not in SPLIT_SEEDS:
        raise ValueError(f"Unknown split {split!r}.")

    seed = SPLIT_SEEDS[split]
    partition = SPLIT_PARTITIONS[split]
    rng = Generator(PCG64(seed))

    actions = partition_of(ACTIONS, partition)
    elements = partition_of(ELEMENTS, partition)
    qualifiers = partition_of(QUALIFIERS, partition)
    off_schedule = partition_of(OFF_SCHEDULE_PHRASES, partition)
    fillers = partition_of(FILLER_PHRASES, partition)

    names = tuple(
        f"{action} {element} {qualifier}"
        for action in actions
        for element in elements
        for qualifier in qualifiers
    )
    required = projects * activities_per_project

    if len(names) < required:
        raise ValueError(
            f"Partition {partition} yields {len(names)} names, fewer than "
            f"the {required} activities required."
        )

    chosen = rng.choice(len(names), size=required, replace=False)

    built_projects: list[SyntheticProject] = []
    cursor = 0

    for index in range(projects):
        project_id = _identifier(split, "project", index)
        activities: list[SyntheticActivity] = []

        for number in range(activities_per_project):
            name = names[int(chosen[cursor])]
            cursor += 1
            activities.append(
                SyntheticActivity(
                    activity_id=_identifier(split, "activity", index, number),
                    project_id=project_id,
                    external_id=f"A{index:02d}-{number:03d}",
                    name=name,
                )
            )

        built_projects.append(
            SyntheticProject(
                project_id=project_id,
                name=f"{split} project {index:02d}",
                activities=tuple(activities),
            )
        )

    total_negatives = projects * negatives_per_project
    textless_total = textless_count(total_negatives)
    textless_slots = {
        int(slot)
        for slot in rng.choice(
            total_negatives, size=textless_total, replace=False
        )
    }

    events: list[SyntheticEvent] = []
    negative_slot = 0

    for index, project in enumerate(built_projects):
        for number in range(positives_per_project):
            source = project.activities[
                int(rng.integers(0, len(project.activities)))
            ]
            transformation = TRANSFORMATIONS[
                int(rng.integers(0, len(TRANSFORMATIONS)))
            ]
            text = _transform(rng, source.name, transformation, fillers)

            draw = float(rng.random())

            if draw < POSITIVE_CORRECT_THRESHOLD:
                identifier_kind = IDENTIFIER_CORRECT
                reported = source.external_id
            elif draw < POSITIVE_INCORRECT_THRESHOLD:
                identifier_kind = IDENTIFIER_INCORRECT_EXISTING
                others = [
                    activity
                    for activity in project.activities
                    if activity.activity_id != source.activity_id
                ]
                reported = others[
                    int(rng.integers(0, len(others)))
                ].external_id
            else:
                identifier_kind = IDENTIFIER_NONE
                reported = None

            events.append(
                SyntheticEvent(
                    event_id=_identifier(split, "positive", index, number),
                    project_id=project.project_id,
                    text=text,
                    reported_activity_id=reported,
                    truth_activity_id=source.activity_id,
                    source_activity_id=source.activity_id,
                    kind=POSITIVE,
                    transformation=transformation,
                    identifier_kind=identifier_kind,
                    textless=False,
                )
            )

        for number in range(negatives_per_project):
            slot = negative_slot
            negative_slot += 1

            source_activity_id: UUID | None = None

            if slot in textless_slots:
                kind = TEXTLESS
                transformation = NOT_DERIVED
                text = TEXTLESS_TEXTS[
                    int(rng.integers(0, len(TEXTLESS_TEXTS)))
                ]
            elif float(rng.random()) < 0.5:
                kind = OFF_SCHEDULE
                transformation = NOT_DERIVED
                phrase = off_schedule[
                    int(rng.integers(0, len(off_schedule)))
                ]
                qualifier = qualifiers[
                    int(rng.integers(0, len(qualifiers)))
                ]
                text = f"{phrase} {qualifier}"
            else:
                kind = CROSS_PROJECT
                other_projects = [
                    candidate
                    for candidate in built_projects
                    if candidate.project_id != project.project_id
                ]
                donor = other_projects[
                    int(rng.integers(0, len(other_projects)))
                ]
                borrowed = donor.activities[
                    int(rng.integers(0, len(donor.activities)))
                ]
                transformation = TRANSFORMATIONS[
                    int(rng.integers(0, len(TRANSFORMATIONS)))
                ]
                text = _transform(
                    rng, borrowed.name, transformation, fillers
                )
                source_activity_id = borrowed.activity_id

            if float(rng.random()) < NEGATIVE_NONEXISTENT_THRESHOLD:
                identifier_kind = IDENTIFIER_NONEXISTENT
                reported = f"{NONEXISTENT_PREFIX}-{slot:04d}"
            else:
                identifier_kind = IDENTIFIER_NONE
                reported = None

            events.append(
                SyntheticEvent(
                    event_id=_identifier(split, "negative", index, number),
                    project_id=project.project_id,
                    text=text,
                    reported_activity_id=reported,
                    # A cross-project source activity is not a valid match
                    # for this event, so the truth stays None.
                    truth_activity_id=None,
                    source_activity_id=source_activity_id,
                    kind=kind,
                    transformation=transformation,
                    identifier_kind=identifier_kind,
                    textless=(kind == TEXTLESS),
                )
            )

    return SyntheticDataset(
        split=split,
        seed=seed,
        partition=partition,
        projects=tuple(built_projects),
        events=tuple(events),
        counts=_count(tuple(built_projects), tuple(events)),
    )


def _count(
    projects: tuple[SyntheticProject, ...],
    events: tuple[SyntheticEvent, ...],
) -> DatasetCounts:
    positives = [e for e in events if e.truth_activity_id is not None]
    negatives = [e for e in events if e.truth_activity_id is None]

    def tally(selected, attribute):
        counts: dict[str, int] = {}

        for event in selected:
            key = getattr(event, attribute)
            counts[key] = counts.get(key, 0) + 1

        return dict(sorted(counts.items()))

    return DatasetCounts(
        projects=len(projects),
        activities=sum(len(p.activities) for p in projects),
        events=len(events),
        positives=len(positives),
        negatives=len(negatives),
        textless_negatives=sum(1 for e in negatives if e.textless),
        event_kinds=tally(events, "kind"),
        positive_identifier_kinds=tally(positives, "identifier_kind"),
        negative_identifier_kinds=tally(negatives, "identifier_kind"),
    )


def validate_dataset(dataset: SyntheticDataset) -> None:
    """Raise if the dataset breaks any contract invariant."""
    failures: list[str] = []

    # Recomputed from the events rather than read from the dataset, so a
    # summary that disagrees with its own events cannot pass validation.
    counts = _count(dataset.projects, dataset.events)

    if counts != dataset.counts:
        failures.append("reported counts disagree with the events")

    for label, actual, minimum in (
        ("projects", counts.projects, MIN_PROJECTS),
        ("activities", counts.activities, MIN_ACTIVITIES),
        ("positives", counts.positives, MIN_POSITIVES),
        ("negatives", counts.negatives, MIN_NEGATIVES),
        ("events", counts.events, MIN_EVENTS),
    ):
        if actual < minimum:
            failures.append(
                f"{label}: {actual} is below the minimum of {minimum}"
            )

    activities = dataset.activities()
    by_project = dataset.activities_by_project()

    project_ids = [p.project_id for p in dataset.projects]
    if len(set(project_ids)) != len(project_ids):
        failures.append("project identifiers are not unique")

    activity_ids = [a.activity_id for a in activities]
    if len(set(activity_ids)) != len(activity_ids):
        failures.append("activity identifiers are not unique")

    normalized = [matching_text(a.name) for a in activities]
    if len(set(normalized)) != len(normalized):
        failures.append("normalized activity names are not unique")

    # No two projects may share a normalized activity name.
    seen: dict[str, UUID] = {}

    for activity in activities:
        key = matching_text(activity.name)
        owner = seen.get(key)

        if owner is not None and owner != activity.project_id:
            failures.append(
                f"normalized name {key!r} appears in two projects"
            )

        seen.setdefault(key, activity.project_id)

    # Every name must come from this split's vocabulary partition, which is
    # what makes cross-split disjointness checkable without the other split.
    allowed_elements = partition_of(ELEMENTS, dataset.partition)
    for activity in activities:
        if not any(element in activity.name for element in allowed_elements):
            failures.append(
                f"activity {activity.external_id} uses an element outside "
                f"partition {dataset.partition}"
            )

    external_ids = {a.external_id: a for a in activities}

    for event in dataset.events:
        if event.project_id not in by_project:
            failures.append(f"event {event.event_id} names no known project")
            continue

        project_activities = by_project[event.project_id]
        project_ids_here = {a.activity_id for a in project_activities}
        project_external = {a.external_id for a in project_activities}

        if event.truth_activity_id is not None:
            # A positive must resolve inside its own project, so a
            # cross-project source can never be read as a valid match.
            if event.truth_activity_id not in project_ids_here:
                failures.append(
                    f"positive {event.event_id} resolves outside its project"
                )

        if event.kind == CROSS_PROJECT:
            if event.truth_activity_id is not None:
                failures.append(
                    f"cross-project event {event.event_id} has a truth"
                )
            if event.source_activity_id is None:
                failures.append(
                    f"cross-project event {event.event_id} lost provenance"
                )
            elif event.source_activity_id in project_ids_here:
                failures.append(
                    f"cross-project event {event.event_id} borrowed from "
                    "its own project"
                )

        if event.textless != (event.kind == TEXTLESS):
            failures.append(f"event {event.event_id} mislabels textlessness")

        if event.textless:
            if matching_text(event.text) != "":
                failures.append(
                    f"textless event {event.event_id} has usable text"
                )
            if event.truth_activity_id is not None:
                failures.append(
                    f"textless event {event.event_id} is not a negative"
                )

        kind = event.identifier_kind
        reported = event.reported_activity_id

        if kind == IDENTIFIER_NONE and reported is not None:
            failures.append(f"event {event.event_id} reports an unexpected id")

        if kind == IDENTIFIER_CORRECT:
            truth = next(
                (
                    a
                    for a in project_activities
                    if a.activity_id == event.truth_activity_id
                ),
                None,
            )
            if truth is None or reported != truth.external_id:
                failures.append(
                    f"event {event.event_id} claims a correct id that is not "
                    "its truth activity"
                )

        if kind == IDENTIFIER_INCORRECT_EXISTING:
            truth = next(
                (
                    a
                    for a in project_activities
                    if a.activity_id == event.truth_activity_id
                ),
                None,
            )
            if reported not in project_external:
                failures.append(
                    f"event {event.event_id} names a missing activity as an "
                    "existing one"
                )
            elif truth is not None and reported == truth.external_id:
                failures.append(
                    f"event {event.event_id} names its truth activity as an "
                    "incorrect one"
                )

        if kind == IDENTIFIER_NONEXISTENT and reported in external_ids:
            failures.append(
                f"event {event.event_id} names an activity that exists"
            )

    expected_textless = textless_count(counts.negatives)
    if counts.textless_negatives != expected_textless:
        failures.append(
            f"textless negatives: {counts.textless_negatives} where "
            f"{expected_textless} were required"
        )

    if failures:
        raise DatasetValidationError(
            "; ".join(sorted(set(failures))[:10])
        )


def development_dataset() -> SyntheticDataset:
    """The development split, generated and validated."""
    dataset = generate_dataset(DEVELOPMENT)
    validate_dataset(dataset)
    return dataset
