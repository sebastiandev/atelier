"""Create a new Project."""

from src.domain.projectstore.dtos import CreateProjectRequest, ProjectRecord
from src.domain.projectstore.ports import ProjectStore


def execute(projectstore: ProjectStore, req: CreateProjectRequest) -> ProjectRecord:
    return projectstore.create_project(req)
