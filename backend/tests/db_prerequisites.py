"""
Prerequisite checks for the database integration tests.

These are pure functions so the policy can be tested directly: whether a
problem is skippable, and whether it must fail regardless of how the suite
was invoked.

The contract's test-execution rules drive two of them. A configured database
other than the dedicated test database is always fatal, so a run can never
fall back to the development database. An unavailable database or one behind
the latest migration is skippable by default, so unit tests remain runnable
without PostgreSQL, but must fail when integration tests were requested
explicitly.
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url


#: The only database the integration fixtures will connect to.
REQUIRED_TEST_DATABASE = "planbridge_test"

PASS = "pass"
SKIP = "skip"
FAIL = "fail"


@dataclass(frozen=True)
class PrerequisiteCheck:
    """The outcome of one prerequisite check."""

    ok: bool
    reason: str | None = None
    #: A fatal problem fails in every mode and is never skipped.
    fatal: bool = False


OK = PrerequisiteCheck(ok=True)


def resolution(check: PrerequisiteCheck, require_db: bool) -> str:
    """Decide what a check means for the run."""
    if check.ok:
        return PASS

    if check.fatal or require_db:
        return FAIL

    return SKIP


def check_env_file(env_file: Path) -> PrerequisiteCheck:
    if env_file.is_file():
        return OK

    return PrerequisiteCheck(
        ok=False,
        reason=(
            f"{env_file.name} is missing; integration tests need a "
            "configured test database."
        ),
    )


def check_database_url(
    url_value: str | None,
    env_file_name: str,
) -> PrerequisiteCheck:
    """
    Confirm the configured URL targets the dedicated test database.

    Both failures are fatal: a missing URL and the wrong database are
    configuration mistakes, and continuing either way risks writing to the
    development database.
    """
    if not url_value:
        return PrerequisiteCheck(
            ok=False,
            fatal=True,
            reason=f"{env_file_name} does not define DATABASE_URL.",
        )

    try:
        database = make_url(url_value).database
    except Exception:
        return PrerequisiteCheck(
            ok=False,
            fatal=True,
            reason=(
                f"DATABASE_URL in {env_file_name} is not a valid "
                "database URL."
            ),
        )

    if database != REQUIRED_TEST_DATABASE:
        return PrerequisiteCheck(
            ok=False,
            fatal=True,
            reason=(
                "Refusing to run integration tests against database "
                f"{database!r}. DATABASE_URL in {env_file_name} must "
                f"target {REQUIRED_TEST_DATABASE!r}."
            ),
        )

    return OK


def check_schema_present(has_alembic_version: bool) -> PrerequisiteCheck:
    if has_alembic_version:
        return OK

    return PrerequisiteCheck(
        ok=False,
        reason=(
            "The test database has no alembic_version table. Apply the "
            "migrations to it before running integration tests."
        ),
    )


def check_revision(
    current: str | None,
    expected: str | None,
) -> PrerequisiteCheck:
    if current == expected:
        return OK

    return PrerequisiteCheck(
        ok=False,
        reason=(
            f"The test database is at revision {current!r}, not head "
            f"{expected!r}. Apply the migrations to it before running "
            "integration tests."
        ),
    )


def check_connection(error: Exception | None) -> PrerequisiteCheck:
    if error is None:
        return OK

    return PrerequisiteCheck(
        ok=False,
        reason=(
            "The test database is unavailable: "
            f"{type(error).__name__}."
        ),
    )
