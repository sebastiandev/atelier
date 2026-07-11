"""Direct-process adapter for deterministic loop checks."""

from __future__ import annotations

import asyncio

from src.domain.loop.dtos import LoopCheckRequest, LoopCheckResult

_OUTPUT_LIMIT = 32_000


class SubprocessLoopCheckRunner:
    """Run a check argv without a shell and return bounded output."""

    async def run(self, request: LoopCheckRequest) -> LoopCheckResult:
        if not request.argv:
            raise ValueError("check command is empty")
        process = await asyncio.create_subprocess_exec(
            *request.argv,
            cwd=request.workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=request.timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            return LoopCheckResult(
                exit_code=None,
                stdout=_decode(stdout),
                stderr=_decode(stderr),
                timed_out=True,
            )
        return LoopCheckResult(
            exit_code=process.returncode,
            stdout=_decode(stdout),
            stderr=_decode(stderr),
        )


def _decode(value: bytes) -> str:
    """Decode and cap captured process output."""
    text = value.decode("utf-8", errors="replace")
    if len(text) <= _OUTPUT_LIMIT:
        return text
    return text[-_OUTPUT_LIMIT:]


__all__ = ["SubprocessLoopCheckRunner"]
