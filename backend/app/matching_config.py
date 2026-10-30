"""
Frozen configuration for activity matching.

Weights and thresholds are configuration, not constants scattered through
the matcher. Every configuration ever used is kept here under its own
version, so a stored MatchResult stays interpretable after the defaults
change: routing for a stored result is recomputed from the configuration it
names, never from the current default.

The registry is append-only. Changing a weight or a threshold requires a new
version rather than an edit, and `config_fingerprint` lets a test pin each
historical entry so an edit fails instead of silently rewriting history.
"""

import hashlib
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Mapping


#: Scores are stored as Numeric(6, 5), so they are rounded to five decimal
#: places before ranking. Ranking a value more precise than the one stored
#: would let the stored order disagree with the computed order.
SCORE_DECIMALS = 5

#: The total order candidates are ranked by, applied in sequence. Recorded
#: here so the scorer and the contract cannot drift apart silently.
TIE_BREAK_ORDER = (
    "combined_score desc",
    "tfidf_score desc",
    "fuzzy_score desc",
    "external_id asc",
    "activity_id asc",
)

#: Identifier for the text normalization the matcher applies. Part of the
#: configuration because changing it changes every score.
PREPROCESSING_V1 = "lower-punct-collapse-1"


class UnknownMatcherVersion(KeyError):
    """Raised for a matcher version that is not in the registry."""


@dataclass(frozen=True)
class MatcherConfig:
    """One complete, immutable matching configuration."""

    version: str
    fuzzy_weight: float
    tfidf_weight: float
    candidate_floor: float
    accept_floor: float
    margin: float
    stored_candidates: int
    preprocessing: str
    rapidfuzz_version: str
    scikit_learn_version: str
    #: True while the thresholds await the development selection round.
    provisional: bool

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("A matcher configuration needs a version.")

        weight_total = self.fuzzy_weight + self.tfidf_weight

        if abs(weight_total - 1.0) > 1e-9:
            raise ValueError(
                "Component weights must sum to 1, so that the combined "
                f"score stays within 0 and 1; got {weight_total}."
            )

        for name in ("fuzzy_weight", "tfidf_weight"):
            value = getattr(self, name)

            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1.")

        for name in ("candidate_floor", "accept_floor", "margin"):
            value = getattr(self, name)

            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1.")

        if self.candidate_floor > self.accept_floor:
            raise ValueError(
                "The candidate floor must not exceed the accept floor; "
                "otherwise a candidate could clear the accept floor and "
                "still be pruned from review."
            )

        # The contract relies on this: a sole candidate's margin equals its
        # combined score, so it can only be reported as ambiguous if the
        # accept floor were below the margin.
        if self.accept_floor < self.margin:
            raise ValueError(
                "The accept floor must be at or above the margin, so that a "
                "sole candidate is never reported as ambiguous."
            )

        if self.stored_candidates < 1:
            raise ValueError("At least one candidate must be stored.")

        if not self.preprocessing:
            raise ValueError("A configuration needs a preprocessing id.")


def config_fingerprint(config: MatcherConfig) -> str:
    """
    A stable digest of a configuration's every field.

    Pinning this per registry entry makes the registry append-only in
    practice: editing a historical configuration changes its fingerprint and
    fails the test that pins it.
    """
    parts = [
        f"{field.name}={getattr(config, field.name)!r}"
        for field in fields(config)
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


MATCHER_VERSION_1 = "planbridge-matcher-1"

_MATCHER_CONFIG_1 = MatcherConfig(
    version=MATCHER_VERSION_1,
    # Provisional, pre-registered values. The development selection round
    # replaces them under a new version; it does not edit this one.
    fuzzy_weight=0.50,
    tfidf_weight=0.50,
    candidate_floor=0.15,
    accept_floor=0.55,
    margin=0.10,
    stored_candidates=5,
    preprocessing=PREPROCESSING_V1,
    rapidfuzz_version="3.14.6",
    scikit_learn_version="1.9.1",
    provisional=True,
)


#: Append-only. Never edit an entry; add a new version instead.
MATCHER_CONFIGS: Mapping[str, MatcherConfig] = MappingProxyType(
    {MATCHER_VERSION_1: _MATCHER_CONFIG_1}
)

CURRENT_MATCHER_VERSION = MATCHER_VERSION_1


def get_matcher_config(version: str) -> MatcherConfig:
    """
    Return the configuration a stored result names.

    An unknown version is an error rather than a fall back to the current
    default: silently reinterpreting an old ranking under new thresholds
    would make it unexplainable.
    """
    try:
        return MATCHER_CONFIGS[version]
    except KeyError:
        raise UnknownMatcherVersion(
            f"No matcher configuration is registered for {version!r}."
        ) from None


def current_matcher_config() -> MatcherConfig:
    """Return the configuration new matching runs use."""
    return MATCHER_CONFIGS[CURRENT_MATCHER_VERSION]
