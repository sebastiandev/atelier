"""The loop/stage/manifest migration converts a v1 tree in place."""

import importlib.util
import json
from pathlib import Path

import yaml

_SPEC = importlib.util.spec_from_file_location(
    "migrate_loops",
    Path(__file__).resolve().parents[4] / "scripts" / "migrate-loops.py",
)
assert _SPEC and _SPEC.loader
migrate_loops = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migrate_loops)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_migrates_layout_shape_and_manifest(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "Atelier"
    repo = tmp_path / "repo"
    _write(
        ws / ".atelier" / "loops" / "lp" / "steps" / "impl.md", "Do the work.\n"
    )
    _write(
        ws / ".atelier" / "loops" / "lp" / "loop.yaml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "lp",
                "name": "LP",
                "steps": [
                    {
                        "id": "impl",
                        "name": "Impl",
                        "kind": "agent_task",
                        "instructions": "steps/impl.md",
                        "transitions": {"pass": "rev"},
                    },
                    {
                        "id": "rev",
                        "from": "code-review",
                        "from_rev": "r1",
                        "transitions": {"pass": "done"},
                    },
                ],
            }
        ),
    )
    _write(
        ws / "works" / "WRK-1" / "planning.json",
        json.dumps({"root_path": str(repo)}),
    )
    _write(
        repo / ".atelier" / "planning" / "WRK-1" / "manifest.json",
        json.dumps(
            {"work_slug": "WRK-1", "artifact_root": "bmad/x", "artifacts": []}
        ),
    )

    monkeypatch.setenv("ATELIER_WORKSPACE_ROOT", str(ws))
    migrate_loops.main([])

    loop = yaml.safe_load((ws / "loops" / "lp" / "loop.yaml").read_text())
    assert "stages" in loop and "steps" not in loop
    assert loop["stages"][0]["instructions"].strip() == "Do the work."
    assert loop["stages"][1]["use"] == "code-review@r1"
    assert "from" not in loop["stages"][1]
    assert not (ws / "loops" / "lp" / "steps").exists()
    assert not (ws / ".atelier").exists()

    manifest = json.loads((ws / "works" / "WRK-1" / "planning" / "manifest.json").read_text())
    assert manifest["plan_artifacts_dir"] == "bmad/x"
    assert "artifact_root" not in manifest
    assert not (ws / "works" / "WRK-1" / "planning.json").exists()
    assert not (repo / ".atelier").exists()


def test_second_run_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "Atelier"
    _write(
        ws / ".atelier" / "stages" / "st" / "stage.yaml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "st",
                "name": "St",
                "outcomes": ["pass"],
                "stage": {
                    "id": "st",
                    "name": "St",
                    "kind": "agent_review",
                    "instructions": "steps/st.md",
                },
            }
        ),
    )
    _write(ws / ".atelier" / "stages" / "st" / "steps" / "st.md", "Review.\n")

    monkeypatch.setenv("ATELIER_WORKSPACE_ROOT", str(ws))
    migrate_loops.main([])
    first = (ws / "stages" / "st" / "stage.yaml").read_text()
    migrate_loops.main([])
    assert (ws / "stages" / "st" / "stage.yaml").read_text() == first
