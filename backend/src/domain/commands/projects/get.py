"""Fetch a Project."""

from src.domain.projectstore.dtos import ProjectRecord
from src.domain.projectstore.ports import ProjectStore


def execute(projectstore: ProjectStore, project_slug: str) -> ProjectRecord | None:
    return projectstore.get_project(project_slug)
