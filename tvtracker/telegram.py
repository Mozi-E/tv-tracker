"""Minimal Telegram Bot API client."""
from . import config


class TelegramError(RuntimeError):
    pass


def _api(method: str, **params):
    import requests  # lazy: keeps offline tests dependency-free

    token = config.telegram_bot_token()
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
    resp = requests.post(
        f"{config.TELEGRAM_BASE}/bot{token}/{method}", json=params, timeout=45
    )
    try:
        data = resp.json()
    except ValueError:
        raise TelegramError(f"{method}: non-JSON reply (HTTP {resp.status_code})")
    if not data.get("ok"):
        raise TelegramError(f"{method} failed: {data}")
    return data["result"]


def get_me():
    return _api("getMe")


def get_updates(offset: int = 0, timeout: int = 0):
    """Long-poll-free fetch of pending messages (workflow runs are short-lived)."""
    return _api(
        "getUpdates", offset=offset, timeout=timeout, allowed_updates=["message"]
    )


def send_message(chat_id, text: str, parse_mode: str = None):
    params = dict(chat_id=chat_id, text=text, disable_web_page_preview=True)
    if parse_mode:
        params["parse_mode"] = parse_mode
    return _api("sendMessage", **params)
