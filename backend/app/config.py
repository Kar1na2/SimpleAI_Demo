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

    # Texted link is {public_base_url}/s/{token}. Default: the local Vite dev server (port 5180).
    # To open links from another device on the tailnet (after scripts/windows/expose-demo.ps1), set
    # PUBLIC_BASE_URL=http://<tailscale-ip>:5180 in backend/.env. Never hard-code an address here.
    public_base_url: str = "http://localhost:5180"
    link_ttl_minutes: int = 30

    # Development-only routes: /api/dev/* (director call info, raw events, emit any event),
    # /ws/calls/{id} (no token) and /api/replay/*. Off unless ENABLE_DEV_ROUTES=true; the director
    # view needs them. Local development only.
    enable_dev_routes: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
