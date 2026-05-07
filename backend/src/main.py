import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.application.http.routes import (
    agents,
    connections,
    health,
    projects,
    providers,
    works,
)
from src.application.ws import agents as ws_agents
from src.domain.connections import ConnectionStoreService
from src.domain.projectstore import ProjectStoreService
from src.domain.projectstore import reconcile as reconcile_projects
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore import WorkStoreService, reconcile
from src.infrastructure.connections import KeyringSecretStore, fetch_context, verify
from src.infrastructure.database import (
    SqlProjectRepository,
    SqlWorkRepository,
    configure_mappings,
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from src.infrastructure.database.connection_repository import SqlConnectionRepository
from src.infrastructure.filesystem import (
    FsProjectFiles,
    FsTranscriptLog,
    FsWorkspaceFiles,
    WorkspacePaths,
)
from src.infrastructure.git import GitWorktreeManager
from src.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app.

    Tests pass a `Settings` with `workspace_root` pointed at a tmp dir so the
    real `~/Atelier/atelier.db` isn't touched. Production calls with no args
    and falls back to env-derived defaults.
    """
    resolved = settings or get_settings()

    # Forward provider credentials from Settings into the process env so
    # SDKs that read os.environ directly (e.g. claude-agent-sdk) pick
    # them up. Done at app build, not lifespan, so test fixtures that
    # construct the app see the same view.
    if resolved.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = resolved.anthropic_api_key

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(resolved)
        configure_mappings()
        initialize_database(engine, workspace_root=resolved.workspace_root)
        session_factory = create_session_factory(engine)

        paths = WorkspacePaths(workspace_root=resolved.workspace_root)
        repo = SqlWorkRepository(session_factory)
        files = FsWorkspaceFiles(paths)
        transcript_log = FsTranscriptLog(paths)

        # Projects reconcile FIRST: works carry a project_slug FK, and the
        # work-side reconcile would fail FK validation if a referenced
        # project hadn't been inserted yet.
        project_repo = SqlProjectRepository(session_factory)
        project_files = FsProjectFiles(paths)
        reconcile_projects(project_repo, project_files)
        projectstore = ProjectStoreService(project_repo, project_files)

        reconcile(repo, files)

        connection_repo = SqlConnectionRepository(session_factory)
        connection_store = ConnectionStoreService(
            connection_repo, KeyringSecretStore(), verify, fetch_context
        )

        worktree_manager = GitWorktreeManager(paths)
        # Orphan sweep: agents persist their slug in SQLite, so on
        # startup any worktree dir whose agent_slug isn't in the live
        # set is left over from a previous run that crashed before
        # tear-down or a soft-deleted work. Run once per work_slug.
        workstore = WorkStoreService(repo, files, transcript_log)
        supervisor = AgentSupervisorService(
            transcript_log, workstore.set_agent_session_id
        )
        for work in workstore.list_works():
            if work.slug is None:
                continue
            live = {a.slug for a in workstore.list_agents_for_work(work.slug) if a.slug}
            worktree_manager.sweep_orphans(work.slug, live)

        app.state.settings = resolved
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.workstore = workstore
        app.state.projectstore = projectstore
        app.state.supervisor = supervisor
        app.state.connection_store = connection_store
        app.state.worktree_manager = worktree_manager
        try:
            yield
        finally:
            await supervisor.shutdown()
            engine.dispose()

    app = FastAPI(title="Atelier", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(works.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    app.include_router(connections.router, prefix="/api")
    app.include_router(ws_agents.router, prefix="/api")
    return app


app = create_app()
