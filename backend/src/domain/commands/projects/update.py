"""Apply a partial update to an existing Project."""

from src.domain.projectstore.dtos import ProjectRecord, UpdateProjectRequest
from src.domain.projectstore.ports import ProjectStore


def execute(projectstore: ProjectStore, req: UpdateProjectRequest) -> ProjectRecord:
    return projectstore.update_project(req)
