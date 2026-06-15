"""OpenCode model discovery via the local CLI."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class OpenCodeModelOption:
    value: str
    label: str


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.:/+-]*$", re.I)


def list_opencode_models(
    *, refresh: bool = False, timeout: float = 12.0
) -> list[OpenCodeModelOption]:
    argv = ["opencode", "models"]
    if refresh:
        argv.append("--refresh")
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("opencode CLI is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("opencode model list timed out") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "opencode models failed").strip()
        raise RuntimeError(_clean_line(message))
    seen: set[str] = set()
    out: list[OpenCodeModelOption] = []
    for raw in result.stdout.splitlines():
        value = _clean_line(raw)
        if not value or value in seen or not _MODEL_RE.match(value):
            continue
        seen.add(value)
        out.append(OpenCodeModelOption(value=value, label=_label_for(value)))
    return out


def _clean_line(value: str) -> str:
    return _ANSI_RE.sub("", value).strip()


def _label_for(value: str) -> str:
    provider, model = value.split("/", 1)
    return f"{_title_token(provider)} / {_title_model(model)}"


def _title_token(value: str) -> str:
    known = {
        "anthropic": "Anthropic",
        "openai": "OpenAI",
        "opencode": "OpenCode",
    }
    return known.get(value.lower(), value.replace("-", " ").replace("_", " ").title())


def _title_model(value: str) -> str:
    parts = value.replace("-", " ").replace("_", " ").split()
    return " ".join(
        part.upper() if part.lower() == "gpt" else part.title() for part in parts
    )


__all__ = ["OpenCodeModelOption", "list_opencode_models"]
