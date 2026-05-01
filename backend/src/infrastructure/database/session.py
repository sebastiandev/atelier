"""Session factory for sync SQLAlchemy.

Routes that need a session take it as a parameter; FastAPI wires it up with
the `session_scope` generator below. Async code paths (WS handlers, supervisor)
that need DB access call sync repository methods via `asyncio.to_thread(...)`.
"""

from collections.abc import Iterator

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """One factory per engine, lifetime of the FastAPI app.

    `expire_on_commit=False` keeps loaded entities usable after a commit, which
    matters for routers that commit and then format the response from the same
    instances.
    """
    return sessionmaker(engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """FastAPI dependency: yields a session, commits on clean exit, rolls back on error.

    Routers consume a `Session` parameter via `Depends(session_dep)` where
    `session_dep` closes over the factory.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
