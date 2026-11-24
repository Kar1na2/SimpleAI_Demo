from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Reads backend/.env (python-dotenv, via pydantic-settings). Values are NOT copied into
    os.environ, so API clients must be given their key explicitly (e.g. Anthropic(api_key=...)).
    Never export ANTHROPIC_API_KEY in the shell: Claude Code would bill it (see CLAUDE.md)."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM agent
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"

    # TTS (OpenAI /v1/audio/speech). Two distinct voices: business side vs. customer side.
    openai_api_key: str | None = None
    openai_tts_model: str = "gpt-4o-mini-tts"
    tts_voice_agent: str = "marin"
    tts_voice_customer: str = "cedar"

    # Matches docker-compose.yml (Postgres 16 on host port 5433).
    database_url: str = "postgresql+asyncpg://simpleai:simpleai@localhost:5433/simpleai"

    # Texted link is {public_base_url}/s/{token}. Default: the Vite dev server (port 5180) on this
    # PC's Tailscale IP, so links open from a Mac on the tailnet once scripts/windows/expose-demo.ps1
    # has run. Override in backend/.env (e.g. http://localhost:5180 for local-only use).
    public_base_url: str = "http://<tailscale-ip>:5180"
    link_ttl_minutes: int = 30

    # Not in CLAUDE.md: mounts /api/dev/* (create call, emit arbitrary event). Off outside local dev.
    enable_dev_routes: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
