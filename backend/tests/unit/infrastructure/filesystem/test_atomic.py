import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.infrastructure.filesystem.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
)


def test_atomic_write_bytes_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "file.bin"
    atomic_write_bytes(target, b"hello\x00world")
    assert target.read_bytes() == b"hello\x00world"


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c" / "file.txt"
    atomic_write_text(target, "hi")
    assert target.read_text() == "hi"


def test_atomic_write_leaves_no_tmp(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "ok")
    siblings = list(tmp_path.iterdir())
    assert siblings == [target]


def test_atomic_write_text_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"


def test_atomic_write_json_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "work.json"
    obj = {"name": "Migration", "tags": ["alpha", "beta"], "n": 7}
    atomic_write_json(target, obj)
    assert json.loads(target.read_text()) == obj


def test_atomic_write_json_handles_path_and_datetime(tmp_path: Path) -> None:
    target = tmp_path / "work.json"
    moment = datetime(2026, 5, 1, 13, 49, tzinfo=UTC)
    folder = Path("/Users/seba/src/atelier")
    atomic_write_json(target, {"folder": folder, "created_at": moment})
    parsed = json.loads(target.read_text())
    assert parsed["folder"] == str(folder)
    assert parsed["created_at"] == moment.isoformat()


def test_atomic_write_json_rejects_unknown_types(tmp_path: Path) -> None:
    target = tmp_path / "x.json"

    class Weird:
        pass

    with pytest.raises(TypeError, match="not JSON serializable"):
        atomic_write_json(target, {"weird": Weird()})


def test_atomic_write_json_is_human_readable(tmp_path: Path) -> None:
    target = tmp_path / "work.json"
    atomic_write_json(target, {"a": 1, "b": 2})
    text = target.read_text()
    assert "\n" in text  # indented
    assert text.startswith("{")


def test_atomic_write_keeps_old_contents_if_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash mid-write must not corrupt the target file."""
    target = tmp_path / "x.txt"
    atomic_write_text(target, "original")

    import os as os_module

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated crash")

    monkeypatch.setattr(os_module, "replace", boom)
    with pytest.raises(OSError, match="simulated crash"):
        atomic_write_text(target, "new content")

    assert target.read_text() == "original"
