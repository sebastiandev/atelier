"""Delete a Project. Attached works are demoted to "loose" via the SQL
FK ``ON DELETE SET NULL`` rule; the FS dir is removed best-effort."""

from src.domain.projectstore.ports import ProjectStore


def execute(projectstore: ProjectStore, project_slug: str) -> None:
    projectstore.delete_project(project_slug)
