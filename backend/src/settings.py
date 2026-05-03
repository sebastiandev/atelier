from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", extra="ignore")

    backend_host: str = "127.0.0.1"
    backend_port: int = 8001
    workspace_root: Path = Path.home() / "Atelier"
    # Anthropic credentials for the Claude Agent SDK adapter. The SDK
    # reads ``ANTHROPIC_API_KEY`` from os.environ directly, so the
    # lifespan forwards this into the environment at startup.
    anthropic_api_key: str | None = None


def get_settings() -> Settings:
    return Settings()
