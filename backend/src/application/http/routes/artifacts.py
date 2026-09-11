"""Artifacts REST router.

Single endpoint today: revealing the underlying file for a doc-type
artifact. Listing happens under the work parent (``works/{slug}/artifacts``)
because that's the natural traversal; reveal is keyed by the artifact's
own slug because the FE knows it from the rail row.
"""

import subprocess

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from src.application.http.routes.works import (
    AgentAdapterFactoryDep,
    ConnectionStoreDep,
    SettingsDep,
    ShareProvisionerDep,
    ShareStoreDep,
    SupervisorDep,
    WorkStoreDep,
    WorktreeDep,
)
from src.application.http.schemas import (
    PrArtifactViewResponse,
    PrOpenerResponse,
    SendPrArtifactFeedbackRequest,
    SendPrArtifactFeedbackResponse,
)
from src.domain.agents.launch import (
    AgentFolderMissing,
    InvalidProviderConfig,
    WorkNotActive,
)
from src.domain.commands.artifacts import pr_feedback
from src.domain.supervisor import AgentTerminated
from src.domain.worktrees import WorktreeProvisionFailed
from src.infrastructure.filesystem.reveal import open_in_file_browser

router = APIRouter()


class RefreshPrStatusesResponse(BaseModel):
    """Result of a PR-status refresh request.

    ``ran=False`` when the call was throttled or the poller isn't
    available; counts are zero in that case. ``ran=True`` with all
    counts at zero means the work has no non-terminal PR rows to
    check, which is the steady-state case for completed work.
    """

    ran: bool
    checked: int = 0
    updated: int = 0
    skipped: int = 0
    not_modified: int = 0


@router.post("/artifacts/refresh-pr-statuses")
async def refresh_pr_statuses_endpoint(
    request: Request,
) -> RefreshPrStatusesResponse:
    """Trigger an out-of-band PR-status refresh.

    Called by the FE when a WorkView mounts so a freshly-opened tab
    sees fresh statuses without waiting up to 5 minutes for the
    scheduled cycle. The poller throttles repeat calls within ~30s so
    bouncing between tabs doesn't fan out hundreds of GitHub fetches.

    Returns ``ran=false`` when the throttle short-circuits — the
    frontend treats that as 'cached data is current enough', no
    follow-up refetch needed.
    """
    poller = getattr(request.app.state, "pr_status_poller", None)
    if poller is None:
        return RefreshPrStatusesResponse(ran=False)
    result = await poller.refresh_now()
    if result is None:
        return RefreshPrStatusesResponse(ran=False)
    return RefreshPrStatusesResponse(
        ran=True,
        checked=result.checked,
        updated=result.updated,
        skipped=result.skipped,
        not_modified=result.not_modified,
    )


@router.post(
    "/artifacts/{artifact_slug}/reveal", status_code=status.HTTP_204_NO_CONTENT
)
def reveal_artifact_endpoint(
    artifact_slug: str, workstore: WorkStoreDep
) -> None:
    """Open a doc-type artifact in the OS file browser. Symmetric with
    the work / agent reveal endpoints — shells out to ``open`` /
    ``xdg-open`` / ``explorer`` depending on platform.

    404 if the slug is unknown; 422 if the artifact isn't a doc or has
    no resolved path."""
    artifact = workstore.get_artifact_by_slug(artifact_slug)
    if artifact is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"artifact not found: {artifact_slug}"
        )
    if artifact.type != "doc" or not artifact.doc_path:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reveal is only supported for doc-type artifacts",
        )
    try:
        open_in_file_browser(artifact.doc_path)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"reveal failed: {exc}",
        ) from exc


@router.get(
    "/works/{work_slug}/artifacts/{artifact_slug}/pr",
    response_model=PrArtifactViewResponse,
)
async def get_pr_artifact_view(
    work_slug: str,
    artifact_slug: str,
    request: Request,
    workstore: WorkStoreDep,
    refresh: bool = True,
    force: bool = False,
) -> PrArtifactViewResponse:
    """Story PR view: fetch the PR's threads (unless ``refresh=false``), post
    any replies owed for pushed feedback, and return the stored state."""
    try:
        if refresh:
            poller = getattr(request.app.state, "pr_status_poller", None)
            gateway = poller.lifecycle_gateway() if poller is not None else None
            if gateway is not None:
                try:
                    view = await pr_feedback.refresh(
                        workstore,
                        gateway,
                        pr_feedback.RefreshRequest(
                            work_slug=work_slug, artifact_slug=artifact_slug, force=force
                        ),
                    )
                    return _to_pr_view(view)
                except pr_feedback.PrUnavailable:
                    # Stale state beats an empty page: fall through to what
                    # the row already holds.
                    pass
        view = pr_feedback.get_view(workstore, work_slug, artifact_slug)
    except pr_feedback.PrArtifactNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_pr_view(view)


@router.post(
    "/works/{work_slug}/artifacts/{artifact_slug}/pr/feedback",
    response_model=SendPrArtifactFeedbackResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_pr_artifact_feedback(
    work_slug: str,
    artifact_slug: str,
    payload: SendPrArtifactFeedbackRequest,
    workstore: WorkStoreDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    settings: SettingsDep,
) -> SendPrArtifactFeedbackResponse:
    """Send selected threads to the agent that opened the PR (relaunching it
    from its snapshot when it was removed)."""
    try:
        result = await pr_feedback.send_feedback(
            workstore,
            supervisor,
            pr_feedback.LaunchDeps(
                worktree_manager=worktree_manager,
                connection_store=connection_store,
                sharestore=sharestore,
                share_provisioner=share_provisioner,
                adapter_factory=adapter_factory,
                settings=settings,
            ),
            pr_feedback.SendFeedbackRequest(
                work_slug=work_slug,
                artifact_slug=artifact_slug,
                mode=payload.mode,
                comment_ids=tuple(item.comment_id for item in payload.comments),
                instructions={
                    item.comment_id: item.instruction
                    for item in payload.comments
                    if item.instruction
                },
                note=payload.note,
            ),
        )
    except pr_feedback.PrArtifactNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (pr_feedback.OpenerUnknown, WorkNotActive, AgentTerminated) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except (
        pr_feedback.FeedbackInvalid,
        InvalidProviderConfig,
        AgentFolderMissing,
        WorktreeProvisionFailed,
    ) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return SendPrArtifactFeedbackResponse(
        view=_to_pr_view(result.view),
        target_slug=result.target_slug,
        relaunched=result.relaunched,
    )


def _to_pr_view(view: pr_feedback.PrView) -> PrArtifactViewResponse:
    artifact = view.artifact
    spec = view.lifecycle.get("opener_spec")
    opened_by: PrOpenerResponse | None = None
    if view.opener is not None:
        opened_by = PrOpenerResponse(
            slug=view.opener.slug,
            name=view.opener.name,
            persona=view.opener.persona,
            present=view.opener.status not in ("stopped", "detached"),
            status=view.opener.status,
        )
    elif isinstance(spec, dict) and spec.get("provider"):
        opened_by = PrOpenerResponse(
            slug=None,
            name=str(spec.get("name") or "Agent"),
            persona=spec.get("persona") or "developer",
            present=False,
        )
    assert artifact.slug is not None
    return PrArtifactViewResponse(
        slug=artifact.slug,
        url=artifact.url,
        status=artifact.status,
        title=artifact.title,
        artifact_id=(
            view.opener.artifact_id
            if view.opener is not None
            else (spec.get("artifact_id") if isinstance(spec, dict) else None)
        ),
        pr=view.lifecycle.get("pr"),
        comments=[row for row in view.lifecycle.get("comments") or [] if isinstance(row, dict)],
        feedback=[row for row in view.lifecycle.get("feedback") or [] if isinstance(row, dict)],
        opened_by=opened_by,
    )
