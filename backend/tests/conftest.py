"""
Shared pytest fixtures.

The integration fixtures below connect to the dedicated planbridge_test
database described by backend/.env.test. They deliberately build their own
engine rather than using app.database.get_engine(), which resolves the
application environment and so targets the development database.

Nothing here creates, drops, truncates or migrates a database. The test
database schema is applied manually with Alembic; these fixtures only verify
that it is already at head.

By default a missing or outdated test database skips the integration tests,
so the unit tests stay runnable without PostgreSQL. Pass --require-db, as an
explicitly requested integration run should, and those conditions fail
instead of skipping. A database other than planbridge_test always fails, in
either mode.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models import Project
from tests.db_prerequisites import (
    FAIL,
    PASS,
    PrerequisiteCheck,
    check_connection,
    check_database_url,
    check_env_file,
    check_revision,
    check_schema_present,
    resolution,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
TEST_ENV_FILE = BACKEND_DIR / ".env.test"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def pytest_addoption(parser):
    parser.addoption(
        "--require-db",
        action="store_true",
        default=False,
        help=(
            "Fail instead of skipping when the test database is "
            "unavailable or behind the latest migration. Use this when "
            "running the integration tests on purpose, so a run cannot "
            "report success having skipped them."
        ),
    )


@pytest.fixture(scope="session")
def require_db(pytestconfig) -> bool:
    return bool(pytestconfig.getoption("--require-db"))


def _enforce(check: PrerequisiteCheck, require_db: bool) -> None:
    """Apply a prerequisite check to the current run."""
    decision = resolution(check, require_db)

    if decision == PASS:
        return

    if decision == FAIL:
        pytest.fail(check.reason, pytrace=False)

    pytest.skip(check.reason)


def _expected_head() -> str:
    """Read the latest migration revision without importing migrations/env.py."""
    script_directory = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    return script_directory.get_current_head()


@pytest.fixture(scope="session")
def test_database_url(require_db) -> str:
    """
    Resolve the test database URL and refuse anything but planbridge_test.

    This is the structural guard against an integration test reaching the
    development database: the development URL cannot satisfy it, in any mode.
    """
    _enforce(check_env_file(TEST_ENV_FILE), require_db)

    url_value = dotenv_values(TEST_ENV_FILE).get("DATABASE_URL")

    _enforce(
        check_database_url(url_value, TEST_ENV_FILE.name),
        require_db,
    )

    return url_value


@pytest.fixture(scope="session")
def test_engine(test_database_url, require_db):
    """A dedicated engine for the test database, verified to be at head."""
    engine = create_engine(test_database_url, poolclass=NullPool)

    current = None
    schema_present = False
    connection_error: Exception | None = None

    try:
        with engine.connect() as connection:
            schema_present = inspect(connection).has_table(
                "alembic_version"
            )

            if schema_present:
                current = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
    except Exception as exc:  # noqa: BLE001 — classified below, not raised
        connection_error = exc

    if connection_error is not None or not schema_present:
        engine.dispose()
        _enforce(check_connection(connection_error), require_db)
        _enforce(check_schema_present(schema_present), require_db)

    check = check_revision(current, _expected_head())

    if not check.ok:
        engine.dispose()
        _enforce(check, require_db)

    yield engine

    engine.dispose()


@pytest.fixture
def db_connection(test_engine):
    """
    A connection wrapped in a transaction that is always rolled back.

    Because the outer transaction never commits, no test data reaches the
    test database permanently and no truncation or cleanup is required.
    """
    connection = test_engine.connect()
    transaction = connection.begin()

    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def db_session(db_connection):
    """
    A real Session whose commits resolve against a SAVEPOINT.

    join_transaction_mode="create_savepoint" lets code under test call
    commit() and rollback() for real while the enclosing transaction stays
    open for the fixture to roll back.
    """
    session = Session(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
    )

    try:
        yield session
    finally:
        session.close()


def _make_project(session: Session, name: str) -> Project:
    project = Project(name=name)
    session.add(project)
    session.flush()
    return project


@pytest.fixture
def project(db_session) -> Project:
    """A project owning the activities under test."""
    return _make_project(db_session, f"Test project {uuid4().hex[:8]}")


@pytest.fixture
def project_b(db_session) -> Project:
    """A second, unrelated project, for project-scoping assertions."""
    return _make_project(db_session, f"Other project {uuid4().hex[:8]}")
