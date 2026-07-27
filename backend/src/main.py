import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from src.application.http.routes import (
    agents,
    artifacts,
    chats,
    connections,
    fs,
    git,
    health,
    loops,
    projects,
    providers,
    shared_folders,
    stages,
    update_status,
    works,
)
from src.application.http.routes import (
    settings as settings_route,
)
from src.application.ws import agents as ws_agents
from src.application.ws import chats as ws_chats
from src.domain.agents import record_artifact
from src.domain.chatstore import ChatStoreService
from src.domain.commands.loops import objective_runs
from src.domain.commands.planning import run_monitor as planning_run_monitor
from src.domain.connections import ConnectionStoreService
from src.domain.loop.dtos import LoopRunSourceKind, LoopStatus
from src.domain.models import Artifact
from src.domain.projectstore import ProjectStoreService
from src.domain.projectstore import reconcile as reconcile_projects
from src.domain.sharedfolders import SharedFolderStoreService
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore import WorkStoreService, reconcile
from src.infrastructure.agents.compaction_sessions import (
    AdapterCompactionSessionClient,
)
from src.infrastructure.agents.factory import ConfiguredAgentAdapterFactory
from src.infrastructure.artifacts.pr_status_poller import PrStatusPoller
from src.infrastructure.connections import KeyringSecretStore, fetch_context, verify
from src.infrastructure.database import (
    SqlChatRepository,
    SqlLoopRunRepository,
    SqlPlanningSessionRepository,
    SqlProjectRepository,
    SqlWorkRepository,
    configure_mappings,
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from src.infrastructure.database.connection_repository import SqlConnectionRepository
from src.infrastructure.database.shared_folder_repository import SqlShareRepository
from src.infrastructure.database.user_settings_repository import (
    SqlUserSettingsRepository,
)
from src.infrastructure.filesystem import (
    FsChatFiles,
    FsChatTranscriptLog,
    FsLoopDefinitionRepository,
    FsPlanningFiles,
    FsProjectFiles,
    FsStageDefinitionRepository,
    FsTranscriptLog,
    FsWorkspaceFiles,
    WorkspacePaths,
)
from src.infrastructure.filesystem.share_provisioner import FsShareProvisioner
from src.infrastructure.git import GitWorktreeManager
from src.infrastructure.loop_check_runner import SubprocessLoopCheckRunner
from src.infrastructure.loop_context_resolver import FilesystemLoopContextResolver
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
        planning_files = FsPlanningFiles(paths)
        planning_sessions = SqlPlanningSessionRepository(session_factory)
        loop_definitions = FsLoopDefinitionRepository(str(resolved.workspace_root))
        stage_definitions = FsStageDefinitionRepository()
        loop_check_runner = SubprocessLoopCheckRunner()
        loop_context_resolver = FilesystemLoopContextResolver()
        loop_runs = SqlLoopRunRepository(session_factory)
        agent_adapter_factory = ConfiguredAgentAdapterFactory(resolved)
        transcript_log = FsTranscriptLog(paths)

        # Projects reconcile FIRST: works carry a project_slug FK, and the
        # work-side reconcile would fail FK validation if a referenced
        # project hadn't been inserted yet.
        project_repo = SqlProjectRepository(session_factory)
        project_files = FsProjectFiles(paths)
        reconcile_projects(project_repo, project_files)
        projectstore = ProjectStoreService(project_repo, project_files)

        reconcile(repo, files)

        chat_repo = SqlChatRepository(session_factory)
        chat_files = FsChatFiles(paths)
        chatstore = ChatStoreService(chat_repo, chat_files)
        chat_transcript_log = FsChatTranscriptLog(chat_files, chatstore.touch_chat)

        connection_repo = SqlConnectionRepository(session_factory)
        connection_store = ConnectionStoreService(
            connection_repo, KeyringSecretStore(), verify, fetch_context
        )

        user_settings_repo = SqlUserSettingsRepository(engine)

        worktree_manager = GitWorktreeManager(paths)
        # Orphan sweep: agents persist their slug in SQLite, so on
        # startup any worktree dir whose agent_slug isn't in the live
        # set is left over from a previous run that crashed before
        # tear-down or a soft-deleted work. Run once per work_slug.
        workstore = WorkStoreService(repo, files, transcript_log)
        workstore.backfill_missing_session_ids_from_transcripts()

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
            candidate = paths.worktree_dir(
                work_slug, agent.worktree_slug or agent_slug
            )
            if candidate.exists():
                return candidate
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
                    elif share.slug is not None:
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
            describe_worktree_state=worktree_manager.describe_state,
        )
        chat_supervisor = AgentSupervisorService(
            chat_transcript_log,
            chatstore.set_chat_session_id,
        )
        for work in workstore.list_works():
            if work.slug is None:
                continue
            live = {
                a.worktree_slug or a.slug
                for a in workstore.list_agents_for_work(work.slug)
                if a.slug
            }
            if work.mode == "loop":
                live.add(objective_runs.OBJECTIVE_WORKTREE_SLUG)
            worktree_manager.sweep_orphans(work.slug, live)

        app.state.settings = resolved
        app.state.agent_adapter_factory = agent_adapter_factory
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.workstore = workstore
        app.state.planningfiles = planning_files
        app.state.planning_sessions = planning_sessions
        app.state.work_roots = planning_sessions
        app.state.loop_check_runner = loop_check_runner
        app.state.loop_context_resolver = loop_context_resolver
        app.state.loop_runs = loop_runs
        app.state.loop_definitions = loop_definitions
        app.state.stage_definitions = stage_definitions
        app.state.projectstore = projectstore
        app.state.chatstore = chatstore
        app.state.supervisor = supervisor
        app.state.chat_supervisor = chat_supervisor
        app.state.connection_store = connection_store
        app.state.user_settings_repo = user_settings_repo
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
        app.state.compaction_session_client = AdapterCompactionSessionClient(
            resolved
        )
        planning_run_monitor_tasks: dict[str, asyncio.Task[Any]] = {}
        app.state.planning_run_monitor_tasks = planning_run_monitor_tasks
        for persisted in loop_runs.list_active():
            if persisted.status not in {
                LoopStatus.RUNNING,
                LoopStatus.WAITING_REPORT,
            }:
                continue
            if persisted.source is None:
                run_id = str(persisted.state.get("id") or "")
                if not run_id:
                    continue
                key = f"{persisted.work_slug}:objective:{run_id}"
                planning_run_monitor_tasks[key] = asyncio.create_task(
                    objective_runs.monitor_run(
                        workstore,
                        loop_runs,
                        supervisor,
                        worktree_manager,
                        connection_store,
                        sharestore,
                        share_provisioner,
                        agent_adapter_factory,
                        loop_check_runner,
                        resolved,
                        objective_runs.ObjectiveRunRequest(
                            work_slug=persisted.work_slug,
                            run_id=run_id,
                        ),
                    ),
                    name=f"objective-run-{persisted.work_slug}-{run_id}",
                )
                continue
            if (
                persisted.source.kind is not LoopRunSourceKind.STORY
                or persisted.plan_run_id is None
            ):
                continue
            key = (
                f"{persisted.work_slug}:{persisted.source.ref}:"
                f"{persisted.plan_run_id}"
            )
            planning_run_monitor_tasks[key] = asyncio.create_task(
                planning_run_monitor.execute(
                    workstore,
                    planning_files,
                    supervisor,
                    worktree_manager,
                    connection_store,
                    sharestore,
                    share_provisioner,
                    agent_adapter_factory,
                    loop_check_runner,
                    loop_runs,
                    resolved,
                    planning_run_monitor.MonitorArtifactRunRequest(
                        work_slug=persisted.work_slug,
                        artifact_id=persisted.source.ref,
                        run_id=persisted.plan_run_id,
                    ),
                ),
                name=(
                    f"planning-run-{persisted.work_slug}-"
                    f"{persisted.source.ref}-{persisted.plan_run_id}"
                ),
            )

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
            for task in planning_run_monitor_tasks.values():
                task.cancel()
            if planning_run_monitor_tasks:
                await asyncio.gather(
                    *planning_run_monitor_tasks.values(),
                    return_exceptions=True,
                )
            await update_check_poller.stop()
            await pr_status_poller.stop()
            await chat_supervisor.shutdown()
            await supervisor.shutdown()
            engine.dispose()

    app = FastAPI(title="Atelier", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(works.router, prefix="/api")
    app.include_router(loops.router, prefix="/api")
    app.include_router(stages.router, prefix="/api")
    app.include_router(chats.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    app.include_router(connections.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(fs.router, prefix="/api")
    app.include_router(git.router, prefix="/api")
    app.include_router(shared_folders.router, prefix="/api")
    app.include_router(update_status.router, prefix="/api")
    app.include_router(settings_route.router, prefix="/api")
    app.include_router(ws_agents.router, prefix="/api")
    app.include_router(ws_chats.router, prefix="/api")
    return app


app = create_app()
