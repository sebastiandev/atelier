from pathlib import Path

from src.domain.commands.agents import start
from src.domain.workstore.dtos import WorkChatContextFolder


def test_mount_work_chat_contexts_links_folder_and_reports_writable_root(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "worktree"
    target = tmp_path / "works" / "WRK-001" / "chat-contexts" / "cht-001-context"
    workdir.mkdir()
    target.mkdir(parents=True)
    (target / "context.md").write_text("# Context")

    mounted = start._mount_work_chat_contexts(
        workdir=workdir,
        folders=[
            WorkChatContextFolder(
                name="cht-001-context",
                mount_path="chat/seed",
                chat_slug="CHT-001",
                chat_title="Exploration",
                absolute_path=target,
            )
        ],
    )

    link = workdir / "chat" / "seed"
    assert link.is_symlink()
    assert link.resolve(strict=True) == target
    assert mounted.summaries[0].name == "cht-001-context"
    assert mounted.summaries[0].mount_path == "chat/seed"
    assert mounted.writable_roots == (target.resolve(strict=False),)


def test_mount_work_chat_contexts_skips_conflicting_path(tmp_path: Path) -> None:
    workdir = tmp_path / "worktree"
    target = tmp_path / "context"
    workdir.mkdir()
    target.mkdir()
    (workdir / "existing").mkdir()

    mounted = start._mount_work_chat_contexts(
        workdir=workdir,
        folders=[
            WorkChatContextFolder(
                name="cht-001-context",
                mount_path="existing",
                chat_slug="CHT-001",
                chat_title="Exploration",
                absolute_path=target,
            )
        ],
    )

    assert mounted.summaries == ()
    assert mounted.writable_roots == ()
