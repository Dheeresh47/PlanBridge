"""
Tests for the integration-test prerequisite policy.

These run without a database. They pin two rules from the contract: a
database other than planbridge_test is refused unconditionally, and a
missing or outdated test database must fail rather than skip once
integration tests are requested explicitly.
"""

from pathlib import Path

import pytest
from dotenv import dotenv_values

from tests.conftest import BACKEND_DIR, TEST_ENV_FILE, _enforce
from tests.db_prerequisites import (
    FAIL,
    PASS,
    SKIP,
    OK,
    PrerequisiteCheck,
    REQUIRED_TEST_DATABASE,
    check_connection,
    check_database_url,
    check_env_file,
    check_revision,
    check_schema_present,
    resolution,
)


TEST_URL = f"postgresql+psycopg://u:p@localhost/{REQUIRED_TEST_DATABASE}"
DEV_URL = "postgresql+psycopg://u:p@localhost/planbridge"


def test_a_passing_check_resolves_to_pass():
    assert resolution(OK, require_db=False) == PASS
    assert resolution(OK, require_db=True) == PASS


def test_recoverable_problem_skips_by_default_and_fails_when_required():
    check = PrerequisiteCheck(ok=False, reason="database unavailable")

    assert resolution(check, require_db=False) == SKIP
    assert resolution(check, require_db=True) == FAIL


def test_fatal_problem_fails_in_both_modes():
    check = PrerequisiteCheck(ok=False, reason="wrong database", fatal=True)

    assert resolution(check, require_db=False) == FAIL
    assert resolution(check, require_db=True) == FAIL


def test_wrong_database_is_fatal_and_names_both_databases():
    check = check_database_url(DEV_URL, ".env.test")

    assert not check.ok
    assert check.fatal
    assert "planbridge" in check.reason
    assert REQUIRED_TEST_DATABASE in check.reason

    # Never skipped, whatever the mode: no fallback to development.
    assert resolution(check, require_db=False) == FAIL
    assert resolution(check, require_db=True) == FAIL


def test_the_dedicated_test_database_passes():
    assert check_database_url(TEST_URL, ".env.test") == OK


def test_missing_database_url_is_fatal():
    check = check_database_url(None, ".env.test")

    assert check.fatal
    assert "does not define DATABASE_URL" in check.reason


def test_unparsable_database_url_is_fatal():
    check = check_database_url("not a url at all", ".env.test")

    assert not check.ok
    assert check.fatal


def test_missing_env_file_is_recoverable_but_blocks_the_run():
    check = check_env_file(Path("does-not-exist.env.test"))

    assert not check.ok
    assert not check.fatal
    assert resolution(check, require_db=False) == SKIP
    assert resolution(check, require_db=True) == FAIL


def test_present_env_file_passes():
    assert check_env_file(TEST_ENV_FILE) == OK


def test_absent_schema_is_reported_with_instructions():
    check = check_schema_present(False)

    assert not check.ok
    assert not check.fatal
    assert "Apply the migrations" in check.reason
    assert check_schema_present(True) == OK


def test_outdated_revision_is_reported_with_both_revisions():
    check = check_revision("47f8534a1543", "0e8b1dbb8b0e")

    assert not check.ok
    assert not check.fatal
    assert "47f8534a1543" in check.reason
    assert "0e8b1dbb8b0e" in check.reason
    assert resolution(check, require_db=True) == FAIL


def test_matching_revision_passes():
    assert check_revision("0e8b1dbb8b0e", "0e8b1dbb8b0e") == OK


def test_unmigrated_database_reports_none_clearly():
    check = check_revision(None, "0e8b1dbb8b0e")

    assert not check.ok
    assert "None" in check.reason


def test_connection_failure_is_recoverable_and_names_the_error_type():
    check = check_connection(OSError("connection refused"))

    assert not check.ok
    assert not check.fatal
    assert "OSError" in check.reason
    assert check_connection(None) == OK


def test_require_db_option_is_registered(pytestconfig):
    assert pytestconfig.getoption("--require-db") in (True, False)


def test_require_db_fixture_matches_the_option(require_db, pytestconfig):
    assert require_db == bool(pytestconfig.getoption("--require-db"))


def test_the_configured_test_env_file_targets_the_test_database():
    """The real .env.test must satisfy the unconditional guard."""
    url_value = dotenv_values(TEST_ENV_FILE).get("DATABASE_URL")

    assert check_database_url(url_value, TEST_ENV_FILE.name) == OK


def test_the_development_env_file_would_be_refused():
    """The guard is what stops a run reaching the development database."""
    development_env = BACKEND_DIR / ".env"

    if not development_env.is_file():
        return

    url_value = dotenv_values(development_env).get("DATABASE_URL")
    check = check_database_url(url_value, development_env.name)

    assert not check.ok
    assert check.fatal


def test_enforce_passes_a_satisfied_check():
    _enforce(OK, require_db=True)
    _enforce(OK, require_db=False)


def test_enforce_skips_a_recoverable_problem_by_default():
    """Without the flag, a missing database leaves the unit tests runnable."""
    with pytest.raises(pytest.skip.Exception, match="unavailable"):
        _enforce(
            PrerequisiteCheck(ok=False, reason="database unavailable"),
            require_db=False,
        )


def test_enforce_fails_a_recoverable_problem_when_the_database_is_required():
    """--require-db turns a skipped integration run into a clear failure."""
    with pytest.raises(pytest.fail.Exception, match="unavailable"):
        _enforce(
            PrerequisiteCheck(ok=False, reason="database unavailable"),
            require_db=True,
        )


def test_enforce_fails_an_outdated_revision_when_the_database_is_required():
    with pytest.raises(pytest.fail.Exception, match="not head"):
        _enforce(
            check_revision("47f8534a1543", "0e8b1dbb8b0e"),
            require_db=True,
        )


def test_enforce_fails_a_fatal_problem_even_without_require_db():
    with pytest.raises(pytest.fail.Exception, match="Refusing"):
        _enforce(check_database_url(DEV_URL, ".env.test"), require_db=False)
