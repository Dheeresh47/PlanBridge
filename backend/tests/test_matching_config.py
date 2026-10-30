"""Tests for the frozen matching configuration and its registry."""

import dataclasses

import pytest
import rapidfuzz
import sklearn

from app.matching_config import (
    CURRENT_MATCHER_VERSION,
    MATCHER_CONFIGS,
    MATCHER_VERSION_1,
    PREPROCESSING_V1,
    SCORE_DECIMALS,
    TIE_BREAK_ORDER,
    MatcherConfig,
    UnknownMatcherVersion,
    config_fingerprint,
    current_matcher_config,
    get_matcher_config,
)


def valid_config(**overrides) -> MatcherConfig:
    values = {
        "version": "test-config",
        "fuzzy_weight": 0.5,
        "tfidf_weight": 0.5,
        "candidate_floor": 0.15,
        "accept_floor": 0.55,
        "margin": 0.10,
        "stored_candidates": 5,
        "preprocessing": PREPROCESSING_V1,
        "rapidfuzz_version": "3.14.6",
        "scikit_learn_version": "1.9.1",
        "provisional": True,
    }
    values.update(overrides)
    return MatcherConfig(**values)


def test_the_provisional_configuration_holds_the_agreed_values():
    config = current_matcher_config()

    assert config.version == MATCHER_VERSION_1
    assert config.fuzzy_weight == 0.50
    assert config.tfidf_weight == 0.50
    assert config.candidate_floor == 0.15
    assert config.accept_floor == 0.55
    assert config.margin == 0.10
    assert config.stored_candidates == 5
    assert config.provisional is True


def test_configuration_is_immutable():
    config = current_matcher_config()

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.accept_floor = 0.9


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1"):
        valid_config(fuzzy_weight=0.6, tfidf_weight=0.6)

    with pytest.raises(ValueError, match="sum to 1"):
        valid_config(fuzzy_weight=0.3, tfidf_weight=0.3)

    # The grid's weightings are all valid.
    for fuzzy in (0.30, 0.40, 0.50, 0.60, 0.70):
        valid_config(fuzzy_weight=fuzzy, tfidf_weight=round(1 - fuzzy, 2))


def test_thresholds_must_be_within_zero_and_one():
    for field in ("candidate_floor", "accept_floor", "margin"):
        with pytest.raises(ValueError, match=field):
            valid_config(**{field: 1.5})

        with pytest.raises(ValueError, match=field):
            valid_config(**{field: -0.1})


def test_candidate_floor_must_not_exceed_the_accept_floor():
    """Otherwise a candidate could clear the accept floor yet be pruned."""
    with pytest.raises(ValueError, match="candidate floor"):
        valid_config(candidate_floor=0.60, accept_floor=0.55)


def test_accept_floor_must_be_at_or_above_the_margin():
    """The contract's sole-candidate rule depends on this."""
    with pytest.raises(ValueError, match="never reported as ambiguous"):
        # The candidate floor is lowered too, so the check under test is the
        # one that fires rather than the candidate-floor check before it.
        valid_config(candidate_floor=0.05, accept_floor=0.05, margin=0.20)


def test_at_least_one_candidate_must_be_stored():
    with pytest.raises(ValueError, match="At least one candidate"):
        valid_config(stored_candidates=0)


def test_a_version_and_a_preprocessing_id_are_required():
    with pytest.raises(ValueError, match="needs a version"):
        valid_config(version="")

    with pytest.raises(ValueError, match="preprocessing id"):
        valid_config(preprocessing="")


def test_every_grid_configuration_is_constructible():
    """No cell of the documented search grid is rejected by validation."""
    built = 0

    for fuzzy in (0.30, 0.40, 0.50, 0.60, 0.70):
        for accept in (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
            for margin in (0.05, 0.10, 0.15, 0.20):
                for floor in (0.10, 0.15, 0.20):
                    valid_config(
                        fuzzy_weight=fuzzy,
                        tfidf_weight=round(1 - fuzzy, 2),
                        accept_floor=accept,
                        margin=margin,
                        candidate_floor=floor,
                    )
                    built += 1

    assert built == 5 * 7 * 4 * 3


def test_current_version_resolves_in_the_registry():
    assert CURRENT_MATCHER_VERSION in MATCHER_CONFIGS
    assert get_matcher_config(CURRENT_MATCHER_VERSION) is (
        current_matcher_config()
    )


def test_unknown_version_is_an_error_not_a_fallback():
    """Reinterpreting an old ranking under new thresholds is not allowed."""
    with pytest.raises(UnknownMatcherVersion, match="planbridge-matcher-9"):
        get_matcher_config("planbridge-matcher-9")


def test_registry_is_not_mutable():
    with pytest.raises(TypeError):
        MATCHER_CONFIGS["planbridge-matcher-2"] = current_matcher_config()


def test_registry_entries_are_append_only():
    """
    Pins each historical configuration by fingerprint.

    Editing a registered configuration changes its fingerprint and fails
    here, which is what makes the registry append-only in practice. A new
    version adds a line; it never changes one.
    """
    expected = {
        MATCHER_VERSION_1: (
            "7870b9c57574056f04af267ddc2f3df82084eac37cbb6e53765bafe373b75760"
        )
    }

    actual = {
        version: config_fingerprint(config)
        for version, config in MATCHER_CONFIGS.items()
    }

    assert set(actual) >= set(expected)

    for version, fingerprint in expected.items():
        assert actual[version] == fingerprint, (
            f"configuration {version} changed; register a new version "
            "instead of editing this one"
        )


def test_fingerprint_changes_when_any_field_changes():
    base = valid_config()

    assert config_fingerprint(base) == config_fingerprint(valid_config())
    assert config_fingerprint(base) != config_fingerprint(
        valid_config(margin=0.15)
    )
    assert config_fingerprint(base) != config_fingerprint(
        valid_config(provisional=False)
    )


def test_recorded_library_versions_match_what_is_installed():
    """A silent library upgrade would change scores under one version."""
    config = current_matcher_config()

    assert config.rapidfuzz_version == rapidfuzz.__version__
    assert config.scikit_learn_version == sklearn.__version__


def test_tie_break_order_and_score_precision_are_declared():
    assert SCORE_DECIMALS == 5
    assert TIE_BREAK_ORDER == (
        "combined_score desc",
        "tfidf_score desc",
        "fuzzy_score desc",
        "external_id asc",
        "activity_id asc",
    )
