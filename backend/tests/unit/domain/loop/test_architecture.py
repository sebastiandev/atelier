"""Architecture checks for the Loop execution boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).parents[4]


@pytest.mark.parametrize(
    "relative_root",
    ("src/domain/loop", "src/domain/commands/loops"),
)
def test_loop_layer_does_not_import_planning_or_commands(relative_root: str) -> None:
    """Keep Loop actions and commands independent of feature commands."""
    violations: list[str] = []
    root = _BACKEND_ROOT / relative_root
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        modules.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        for module in modules:
            if module.startswith(("src.domain.planning", "src.domain.commands")):
                violations.append(f"{path.relative_to(_BACKEND_ROOT)} imports {module}")
    assert violations == []


def test_loop_monitor_does_not_use_chat_runtime() -> None:
    """Keep monitored stages on agent and Loop prompts, not exploratory chat."""
    source = (_BACKEND_ROOT / "src/domain/loop/monitor.py").read_text()

    assert "src.domain.chats" not in source
    assert "RegularChatRuntimePrompt" not in source
