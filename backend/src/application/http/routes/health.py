from importlib.metadata import version

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "version": version("atelier-backend")}
