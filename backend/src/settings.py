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
    # Walking-skeleton: dev demo can space stub events out so streaming
    # is visible in the browser. Tests leave at 0 for determinism. Only
    # the stub-backed Amp adapter consults this; real adapters ignore it.
    stub_event_delay: float = 0.0


def get_settings() -> Settings:
    return Settings()
