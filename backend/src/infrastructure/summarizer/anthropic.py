"""Anthropic-backed Summarizer for handoff doc generation.

Single one-shot call to ``POST /v1/messages``. Bypasses the Claude Agent
SDK because that's session-shaped — for a one-off completion we just
want the simplest path. ``httpx`` is already a project dep so no new
package.

Falls back to the structural (no-LLM) summarizer when no API key is
configured, when the API call fails, or when the response is malformed.
The handoff feature stays usable offline; the doc just looks more
mechanical.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.domain.agents.handoffs import (
    SUMMARY_SYSTEM_PROMPT,
    Summarizer,
    SummaryContext,
    format_summary_prompt,
    structural_summarizer,
)

_log = logging.getLogger(__name__)

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096
_TIMEOUT_SECONDS = 60.0

class AnthropicSummarizer:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = _DEFAULT_MODEL,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        # Reusable client lets tests inject a transport mock without
        # patching the global httpx module.
        self._client = client

    def __call__(
        self, events: list[dict[str, Any]], context: SummaryContext
    ) -> str:
        prompt = format_summary_prompt(events, context)
        try:
            client = self._client or httpx.Client(timeout=_TIMEOUT_SECONDS)
            response = client.post(
                _ANTHROPIC_URL,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self._model,
                    "max_tokens": _MAX_TOKENS,
                    "system": SUMMARY_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            payload = response.json()
            blocks = payload.get("content", [])
            text = "".join(
                b.get("text", "") for b in blocks if b.get("type") == "text"
            ).strip()
            if not text:
                raise ValueError("empty response from Anthropic API")
            return text
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            _log.warning(
                "Anthropic summarizer fell back to structural: %s", exc
            )
            return structural_summarizer(events, context)


def build_summarizer(api_key: str | None) -> Summarizer:
    """Factory used at app boot. With an API key, returns the
    Anthropic-backed summarizer; without, returns the structural fallback."""
    if api_key:
        return AnthropicSummarizer(api_key)
    return structural_summarizer
