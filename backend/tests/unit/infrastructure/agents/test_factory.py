"""Unit tests for the build_adapter singledispatch factory."""

from pathlib import Path

import pytest

from src.domain.agents import (
    AmpAgentConfig,
    AmpMode,
    ClaudeAgentConfig,
    ClaudeModel,
    CommonAgentConfig,
)
from src.infrastructure.agents import (
    AmpAdapter,
    ClaudeCodeAdapter,
    build_adapter,
)
from src.settings import Settings


def _common() -> CommonAgentConfig:
    return CommonAgentConfig(
        workdir=Path("/tmp/atelier/test"),
        system_prompt="prompt",
    )


def test_build_claude_adapter() -> None:
    config = ClaudeAgentConfig(common=_common(), model=ClaudeModel.OPUS_4_7)
    adapter = build_adapter(config, Settings())
    assert isinstance(adapter, ClaudeCodeAdapter)


def test_build_amp_adapter() -> None:
    config = AmpAgentConfig(common=_common(), mode=AmpMode.SMART)
    adapter = build_adapter(config, Settings())
    assert isinstance(adapter, AmpAdapter)


def test_build_unregistered_config_raises() -> None:
    class _Unregistered:
        pass

    with pytest.raises(NotImplementedError, match="No adapter registered"):
        build_adapter(_Unregistered(), Settings())  # type: ignore[arg-type]
