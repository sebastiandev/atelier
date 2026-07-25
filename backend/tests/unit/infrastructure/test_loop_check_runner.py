"""Tests for deterministic loop check execution."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from src.domain.loop.dtos import LoopCheckRequest
from src.infrastructure.loop_check_runner import SubprocessLoopCheckRunner


def test_changed_files_token_expands_to_individual_arguments(
    tmp_path: Path,
) -> None:
    runner = SubprocessLoopCheckRunner()

    result = asyncio.run(
        runner.run(
            LoopCheckRequest(
                workdir=tmp_path,
                argv=(
                    sys.executable,
                    "-c",
                    "import sys; print('|'.join(sys.argv[1:]))",
                    "{changed_files}",
                ),
                timeout_seconds=5,
                changed_files=("src/app.py", "tests/test_app.py"),
            )
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "src/app.py|tests/test_app.py"
