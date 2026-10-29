from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """
    Return the application engine, created on first use.

    The engine is deliberately not created at import time: importing any
    module under app would otherwise bind it to whichever environment the
    working directory resolved, which is how tests reach the wrong database.
    Code that must target a specific database — such as the integration-test
    fixtures — should build its own engine instead of using this one.
    """
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Return the application session factory, created on first use."""
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
    )


def get_session() -> Iterator[Session]:
    """Yield a session for one unit of work and always close it."""
    session = get_sessionmaker()()

    try:
        yield session
    finally:
        session.close()
