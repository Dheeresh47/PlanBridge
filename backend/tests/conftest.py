"""
Shared pytest fixtures.

The integration fixtures below connect to the dedicated planbridge_test
database described by backend/.env.test. They deliberately build their own
engine instead of reusing app.database.engine or app.database.SessionLocal,
because those are bound at import time to whichever .env the working
directory resolves — normally the development database.

Nothing here creates, drops, truncates or migrates a database. The test
database schema is applied manually with Alembic; these fixtures only verify
that it is already at head and skip when it is not.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models import Project


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
TEST_ENV_FILE = BACKEND_DIR / ".env.test"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

# The integration fixtures refuse to connect to any other database.
REQUIRED_TEST_DATABASE = "planbridge_test"


def _expected_head() -> str:
    """Read the latest migration revision without importing migrations/env.py."""
    script_directory = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    return script_directory.get_current_head()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """
    Resolve the test database URL and refuse anything but planbridge_test.

    This is the structural guard against an integration test reaching the
    development database: the development URL cannot satisfy it.
    """
    if not TEST_ENV_FILE.is_file():
        pytest.skip(
            f"{TEST_ENV_FILE.name} is missing; "
            "integration tests need a configured test database."
        )

    url_value = dotenv_values(TEST_ENV_FILE).get("DATABASE_URL")

    if not url_value:
        pytest.fail(
            f"{TEST_ENV_FILE.name} does not define DATABASE_URL."
        )

    database = make_url(url_value).database

    if database != REQUIRED_TEST_DATABASE:
        pytest.fail(
            "Refusing to run integration tests against database "
            f"'{database}'. DATABASE_URL in {TEST_ENV_FILE.name} must "
            f"target '{REQUIRED_TEST_DATABASE}'."
        )

    return url_value


@pytest.fixture(scope="session")
def test_engine(test_database_url):
    """A dedicated engine for the test database, verified to be at head."""
    engine = create_engine(test_database_url, poolclass=NullPool)

    try:
        with engine.connect() as connection:
            if not inspect(connection).has_table("alembic_version"):
                pytest.skip(
                    "The test database has no alembic_version table. "
                    "Apply the migrations to it before running "
                    "integration tests."
                )

            current = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
    except Exception as exc:  # noqa: BLE001 — surfaced as a skip, not an error
        engine.dispose()
        pytest.skip(f"The test database is unavailable: {type(exc).__name__}")

    expected = _expected_head()

    if current != expected:
        engine.dispose()
        pytest.skip(
            f"The test database is at revision {current!r}, not head "
            f"{expected!r}. Apply the migrations to it before running "
            "integration tests."
        )

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
