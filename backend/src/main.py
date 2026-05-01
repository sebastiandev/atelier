from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.application.http.routes import health, works
from src.domain.workstore import WorkStoreService, reconcile
from src.infrastructure.database import (
    SqlWorkRepository,
    configure_mappings,
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from src.infrastructure.filesystem import (
    FsTranscriptLog,
    FsWorkspaceFiles,
    WorkspacePaths,
)
from src.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app.

    Tests pass a `Settings` with `workspace_root` pointed at a tmp dir so the
    real `~/Atelier/atelier.db` isn't touched. Production calls with no args
    and falls back to env-derived defaults.
    """
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(resolved)
        configure_mappings()
        initialize_database(engine)
        session_factory = create_session_factory(engine)

        paths = WorkspacePaths(workspace_root=resolved.workspace_root)
        repo = SqlWorkRepository(session_factory)
        files = FsWorkspaceFiles(paths)
        transcript_log = FsTranscriptLog(paths)
        reconcile(repo, files)

        app.state.settings = resolved
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.workstore = WorkStoreService(repo, files, transcript_log)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Atelier", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router, prefix="/api")
    app.include_router(works.router, prefix="/api")
    return app


app = create_app()
