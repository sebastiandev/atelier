from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", extra="ignore")

    backend_host: str = "127.0.0.1"
    backend_port: int = 8001
    workspace_root: Path = Path.home() / "Atelier"
    codex_repo_path: Path | None = None
    # Walking-skeleton stub: dev demo can space events out so streaming is
    # visible in the browser. Tests leave this at 0 for determinism. Real
    # adapters (STORY-011) ignore this entirely.
    stub_event_delay: float = 0.0


def get_settings() -> Settings:
    return Settings()
