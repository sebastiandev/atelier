"""Requests keep accepting the field name they had before the rename."""

import pytest
from pydantic import BaseModel

from src.application.http.schemas import (
    StartPlanningChatRequest,
    StartWorkPlanRequest,
)


@pytest.mark.parametrize(
    "model", [StartWorkPlanRequest, StartPlanningChatRequest], ids=lambda m: m.__name__
)
@pytest.mark.parametrize("field", ["plan_artifacts_dir", "artifact_root_path"])
def test_either_field_name_populates_plan_artifacts_dir(
    model: type[BaseModel], field: str
) -> None:
    """A browser tab loaded from an older build still posts the old name."""
    payload: dict[str, object] = {
        "root_path": "/repo",
        "framework": "bmad",
        "profile": "feature",
        "provider": "amp",
        "model": "smart",
        field: "bmad/custom",
    }

    parsed = model.model_validate(payload)

    assert parsed.plan_artifacts_dir == "bmad/custom"
