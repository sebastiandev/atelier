"""Workarounds for openai-codex-sdk event parsing drift.

The Python SDK's ``FileChangeItem`` currently accepts only terminal patch
statuses (``completed`` / ``failed``), but the Codex CLI streams
``file_change`` items while they are still ``in_progress``. That raises a
Pydantic validation error before Atelier's adapter can normalize the event.

Patch strategy: parse all ``file_change`` items through the SDK's
``UnknownThreadItem`` fallback. It preserves extra fields, including
``changes`` and ``status``, and ``CodexAdapter._normalize_sdk_item`` already
dispatches from the raw ``type`` field. This keeps completed and in-progress
file changes flowing without editing vendored package files.
"""

from __future__ import annotations

import openai_codex_sdk.parsing as _codex_parsing
from openai_codex_sdk.types import UnknownThreadItem

_PATCH_MARKER = "__atelier_codex_sdk_patched__"


def install() -> None:
    """Apply the patch. Idempotent; safe across reloads."""
    if getattr(_codex_parsing, _PATCH_MARKER, False):
        return
    setattr(_codex_parsing, _PATCH_MARKER, True)
    _codex_parsing._ITEM_MODELS["file_change"] = UnknownThreadItem


__all__ = ["install"]
