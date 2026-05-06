"""Unit tests for ``render_agent_contexts``.

Exercises filename generation (clean-slug vs numbered fallback), per-type
body rendering, and the index-grouping shape — all via a stub
``WorkspaceFiles`` so no real filesystem is involved.
"""

from typing import Any

import pytest

from src.domain.agents.context_render import render_agent_contexts
from src.domain.models import Context


class _StubFiles:
    """Captures writes from the renderer so tests can assert on them."""

    def __init__(self) -> None:
        self.context_files: list[tuple[str, str, str, str]] = []
        self.indexes: list[tuple[str, str, str]] = []

    def write_agent_context_file(
        self, work_slug: str, agent_slug: str, filename: str, content: str
    ) -> str:
        self.context_files.append((work_slug, agent_slug, filename, content))
        return f"/ws/works/{work_slug}/agents/{agent_slug}/context/{filename}"

    def write_agent_context_index(
        self, work_slug: str, agent_slug: str, content: str
    ) -> str:
        self.indexes.append((work_slug, agent_slug, content))
        return f"/ws/works/{work_slug}/agents/{agent_slug}/context.md"

    # Unused methods on the WorkspaceFiles port — left as Any so the stub
    # only has to model what the renderer actually calls.
    def __getattr__(self, name: str) -> Any:  # pragma: no cover - safety net
        raise AttributeError(name)


def test_returns_none_for_empty_contexts() -> None:
    files = _StubFiles()
    result = render_agent_contexts(files, "WRK-001", "agt-1", [])
    assert result is None
    assert files.context_files == []
    assert files.indexes == []


def test_clean_slug_value_becomes_filename() -> None:
    files = _StubFiles()
    render_agent_contexts(
        files,
        "WRK-001",
        "agt-1",
        [Context(type="jira", value="ENG-3421", conn_id="con-1")],
        {0: "# ENG-3421\n\nbody\n"},
    )
    [(_, _, filename, _)] = files.context_files
    assert filename == "jira-ENG-3421.md"


def test_unsluggable_value_falls_back_to_numbered_filenames() -> None:
    files = _StubFiles()
    render_agent_contexts(
        files,
        "WRK-001",
        "agt-1",
        [
            Context(type="text", value="multi line\nbody"),
            Context(type="url", value="https://example.com/foo"),
            Context(type="text", value="another"),
        ],
    )
    filenames = [name for _, _, name, _ in files.context_files]
    assert filenames == ["text-1.md", "url-1.md", "text-2.md"]


def test_duplicate_clean_slugs_get_numbered() -> None:
    files = _StubFiles()
    render_agent_contexts(
        files,
        "WRK-001",
        "agt-1",
        [
            Context(type="jira", value="ENG-3421"),
            Context(type="jira", value="ENG-3421"),
        ],
        {0: "first\n", 1: "second\n"},
    )
    filenames = [name for _, _, name, _ in files.context_files]
    assert filenames == ["jira-ENG-3421.md", "jira-1.md"]


def test_text_body_is_just_the_value() -> None:
    files = _StubFiles()
    render_agent_contexts(
        files, "WRK-001", "agt-1", [Context(type="text", value="hello world")]
    )
    [(_, _, _, body)] = files.context_files
    assert body == "hello world\n"


def test_connection_backed_body_uses_fetched_markdown() -> None:
    files = _StubFiles()
    fetched = "# ENG-3421 — Login flaky\n\n- **Status:** Open\n"
    render_agent_contexts(
        files,
        "WRK-001",
        "agt-1",
        [Context(type="jira", value="ENG-3421", conn_id="con-1")],
        {0: fetched},
    )
    [(_, _, _, body)] = files.context_files
    assert body == fetched


def test_connection_backed_body_missing_fetched_raises() -> None:
    """Connection-backed types require a pre-fetched body — the caller
    (start) is responsible. A missing entry is a programmer error,
    not a runtime fallback."""
    files = _StubFiles()
    with pytest.raises(RuntimeError, match="requires a pre-fetched body"):
        render_agent_contexts(
            files,
            "WRK-001",
            "agt-1",
            [Context(type="jira", value="ENG-3421", conn_id="con-1")],
        )


def test_index_groups_entries_by_type() -> None:
    files = _StubFiles()
    render_agent_contexts(
        files,
        "WRK-001",
        "agt-1",
        [
            Context(type="text", value="snippet"),
            Context(type="jira", value="ENG-1"),
            Context(type="text", value="another"),
        ],
        {1: "fetched body\n"},
    )
    [(_, _, index)] = files.indexes
    assert "## Text" in index
    assert "## Jira tickets" in index
    # Both text entries appear under the Text section before Jira.
    text_section = index.index("## Text")
    jira_section = index.index("## Jira tickets")
    assert text_section < jira_section
    assert index.count("- [text-") == 2
    assert "[jira-ENG-1.md](context/jira-ENG-1.md)" in index


def test_returns_index_path_from_files_port() -> None:
    files = _StubFiles()
    path = render_agent_contexts(
        files, "WRK-001", "agt-1", [Context(type="text", value="x")]
    )
    assert path == "/ws/works/WRK-001/agents/agt-1/context.md"
