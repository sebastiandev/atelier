import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from src.application.http.routes import (
    agents,
    artifacts,
    connections,
    fs,
    git,
    health,
    projects,
    providers,
    shared_folders,
    update_status,
    works,
)
from src.application.ws import agents as ws_agents
from src.domain.agents import record_artifact
from src.domain.connections import ConnectionStoreService
from src.domain.models import Artifact
from src.domain.projectstore import ProjectStoreService
from src.domain.projectstore import reconcile as reconcile_projects
from src.domain.sharedfolders import SharedFolderStoreService
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
from src.infrastructure.database.shared_folder_repository import SqlShareRepository
from src.infrastructure.filesystem import (
    FsProjectFiles,
    FsTranscriptLog,
    FsWorkspaceFiles,
    WorkspacePaths,
)
from src.infrastructure.filesystem.share_provisioner import FsShareProvisioner
from src.infrastructure.artifacts.pr_status_poller import PrStatusPoller
from src.infrastructure.git import GitWorktreeManager
from src.infrastructure.summarizer import build_summarizer
from src.infrastructure.update_check import GitUpdateChecker, UpdateCheckPoller
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
    if resolved.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = resolved.openai_api_key

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

        # Shared folders: project-scoped, persistent across agent
        # worktrees. Provisioner owns the filesystem side (canonical
        # dirs, external symlinks, worktree-side mounts); the service
        # composes it with the SQL repository.
        share_repo = SqlShareRepository(session_factory)
        share_provisioner = FsShareProvisioner(paths)

        def _resolve_project_id(slug: str) -> int | None:
            project = project_repo.get_project_by_slug(slug)
            return project.id if project is not None else None

        sharestore = SharedFolderStoreService(
            share_repo, share_provisioner, _resolve_project_id
        )

        # Resolve an agent's actual working directory. The per-agent
        # worktree if provisioned, the source folder otherwise.
        def _resolve_workdir(work_slug: str, agent_slug: str) -> Path:
            candidate = paths.worktree_dir(work_slug, agent_slug)
            if candidate.exists():
                return candidate
            agent = next(
                (
                    a
                    for a in workstore.list_agents_for_work(work_slug)
                    if a.slug == agent_slug
                ),
                None,
            )
            if agent is None:
                raise ValueError(f"agent not found: {agent_slug}")
            return agent.folder

        # The full set of filesystem roots an agent is allowed to drop a
        # doc artifact under: its worktree first (used to resolve
        # relative paths), then every shared folder registered on the
        # parent project. The validator accepts a doc path if its
        # resolved real path lives inside any of these.
        def _resolve_allowed_roots(work_slug: str, agent_slug: str) -> list[Path]:
            roots: list[Path] = [_resolve_workdir(work_slug, agent_slug)]
            record = workstore.get_work(work_slug)
            project_slug = record.work.project_slug if record is not None else None
            if project_slug is not None:
                for share in sharestore.list_for_project(project_slug):
                    # Custom-location shares: prefer the real path so we
                    # match the symlink target. Default-location shares:
                    # the canonical dir under the workspace.
                    if share.real_path is not None:
                        roots.append(share.real_path)
                    else:
                        roots.append(paths.project_share_dir(project_slug, share.slug))
            return roots

        def _track_artifact(
            work_slug: str, agent_slug: str, payload: dict[str, Any]
        ) -> Artifact:
            return record_artifact(
                work_slug,
                agent_slug,
                payload,
                workstore=workstore,
                resolve_allowed_roots=_resolve_allowed_roots,
            )

        supervisor = AgentSupervisorService(
            transcript_log,
            workstore.set_agent_session_id,
            record_artifact=_track_artifact,
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
        app.state.workspace_paths = paths
        # Shared folders state for the routes + agent lifecycle hooks.
        app.state.sharestore = sharestore
        app.state.share_provisioner = share_provisioner
        # Surfaced for the handoff route (reads the source agent's NDJSON
        # to build the doc). Same instance the supervisor writes through.
        app.state.transcript_log = transcript_log
        # Anthropic-backed when an API key is set, structural fallback
        # otherwise — keeps the handoff feature usable offline.
        app.state.summarizer = build_summarizer(resolved.anthropic_api_key)

        # Background loop that refreshes non-terminal PR artifact
        # statuses against GitHub every 5 minutes. No-op when the user
        # doesn't track any PRs — the cycle's first query short-
        # circuits with an empty list and we never hit the network.
        pr_status_poller = PrStatusPoller(workstore)
        pr_status_poller.start()
        app.state.pr_status_poller = pr_status_poller

        # Background loop that compares this checkout to origin/main
        # every 2h so the frontend can show an "update available"
        # chip. The checker is inert (returns None) on non-git or
        # offline hosts; the route degrades to available=false in
        # that case.
        update_checker = GitUpdateChecker()
        update_check_poller = UpdateCheckPoller(update_checker)
        update_check_poller.start()
        app.state.update_checker = update_checker
        app.state.update_check_poller = update_check_poller

        try:
            yield
        finally:
            await update_check_poller.stop()
            await pr_status_poller.stop()
            await supervisor.shutdown()
            engine.dispose()

    app = FastAPI(title="Atelier", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(works.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    app.include_router(connections.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(fs.router, prefix="/api")
    app.include_router(git.router, prefix="/api")
    app.include_router(shared_folders.router, prefix="/api")
    app.include_router(update_status.router, prefix="/api")
    app.include_router(ws_agents.router, prefix="/api")
    return app


app = create_app()
