"""Shared pytest fixtures.

The fixtures here keep tests isolated from the user's real `~/Atelier/`:
each test gets its own tmp workspace, its own SQLite file, and (where it
asks for one) its own engine with mappings configured and schema migrated.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from src.infrastructure.database import (
    configure_mappings,
    create_database_engine,
    initialize_database,
)
from src.main import create_app
from src.settings import Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Settings whose `workspace_root` is an isolated tmp directory."""
    return Settings(workspace_root=tmp_path / "Atelier")


@pytest.fixture
def isolated_engine(test_settings: Settings) -> Iterator[Engine]:
    """Engine pointed at a tmp workspace, mappings configured, schema migrated."""
    engine = create_database_engine(test_settings)
    configure_mappings()
    initialize_database(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def app_client(test_settings: Settings) -> Iterator[TestClient]:
    """FastAPI TestClient with full lifespan (engine + migrations) on a tmp workspace."""
    app = create_app(test_settings)
    with TestClient(app) as client:
        yield client
