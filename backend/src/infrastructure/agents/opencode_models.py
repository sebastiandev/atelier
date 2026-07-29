"""OpenCode model discovery via the local CLI."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class OpenCodeModelOption:
    """One selectable OpenCode model.

    ``effort_values`` are the model's *variants* — OpenCode's name for
    reasoning effort. They are per-model and irregular (Haiku offers
    ``high``/``max``, Opus 5 offers ``low``..``max``, ``big-pickle``
    offers none), so an empty tuple means this model has no effort dial
    at all and the UI should hide it rather than offer a value OpenCode
    would ignore.
    """

    value: str
    label: str
    effort_values: tuple[str, ...] = ()


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.:/+-]*$", re.I)
_VERBOSE_ID_RE = re.compile(
    r"^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.:/+-]*$", re.I | re.M
)


def list_opencode_models(
    *, refresh: bool = False, timeout: float = 20.0
) -> list[OpenCodeModelOption]:
    # --verbose adds a JSON block per model, which is the only place the
    # CLI publishes each model's variants. Older CLIs reject the flag, so
    # fall back to the plain listing and simply report no variants.
    text = _run_models(verbose=True, refresh=refresh, timeout=timeout)
    if text is None:
        text = _run_models(verbose=False, refresh=refresh, timeout=timeout)
    if text is None:
        raise RuntimeError("opencode models failed")
    variants = _parse_variants(text)
    seen: set[str] = set()
    out: list[OpenCodeModelOption] = []
    for raw in text.splitlines():
        value = _clean_line(raw)
        if not value or value in seen or not _MODEL_RE.match(value):
            continue
        seen.add(value)
        out.append(
            OpenCodeModelOption(
                value=value,
                label=_label_for(value),
                effort_values=variants.get(value, ()),
            )
        )
    return out


def _run_models(*, verbose: bool, refresh: bool, timeout: float) -> str | None:
    """Run ``opencode models``; return stdout, or None when the CLI
    rejected the invocation (so the caller can retry a simpler one)."""
    argv = ["opencode", "models"]
    if verbose:
        argv.append("--verbose")
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
        if verbose:
            return None
        message = (result.stderr or result.stdout or "opencode models failed").strip()
        raise RuntimeError(_clean_line(message))
    return _ANSI_RE.sub("", result.stdout)


def _parse_variants(text: str) -> dict[str, tuple[str, ...]]:
    """Pull each model's variant names out of ``--verbose`` output.

    The CLI prints ``provider/model`` on its own line followed by a JSON
    object, repeated — not a JSON array — so decode block by block and
    skip anything that doesn't parse.
    """
    decoder = json.JSONDecoder()
    out: dict[str, tuple[str, ...]] = {}
    for match in _VERBOSE_ID_RE.finditer(text):
        start = text.find("{", match.end())
        if start < 0:
            continue
        try:
            payload, _ = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        variants = payload.get("variants")
        if not isinstance(variants, dict):
            continue
        names = tuple(str(name) for name in variants if str(name).strip())
        if names:
            out[match.group(0)] = names
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
