"""List all Projects."""

from src.domain.models import Project
from src.domain.projectstore.ports import ProjectStore


def execute(projectstore: ProjectStore) -> list[Project]:
    return projectstore.list_projects()
