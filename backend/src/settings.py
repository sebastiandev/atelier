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
    # OpenAI credentials for the Codex SDK adapter. Same pattern as
    # ``anthropic_api_key``: the Codex SDK reads ``OPENAI_API_KEY`` from
    # os.environ, and ``create_app`` forwards this into the environment
    # at startup so dev .env.local flows pick it up. Promoted to a real
    # ``codex`` Connection (alongside the Anthropic one) by a follow-up
    # story so multi-account use stops sharing a single env var.
    openai_api_key: str | None = None


def get_settings() -> Settings:
    return Settings()
