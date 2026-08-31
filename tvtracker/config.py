"""Runtime configuration, read from environment variables.

Secrets are NEVER hard-coded. Locally they come from a `.env` file (see
`.env.example`); in GitHub Actions they come from repository secrets.
Everything is read lazily so tests can set env vars before calling.
"""
import os

TMDB_BASE = "https://api.themoviedb.org/3"
TELEGRAM_BASE = "https://api.telegram.org"


def tmdb_api_key() -> str:
    return os.environ.get("TMDB_API_KEY", "").strip()


def telegram_bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def telegram_chat_id() -> str:
    """Optional. If set, notifications always go here too (handy before /start)."""
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def data_dir() -> str:
    return os.environ.get("TV_TRACKER_DATA_DIR", "data")
