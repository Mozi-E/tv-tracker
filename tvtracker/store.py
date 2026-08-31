"""Load/save the two JSON files that live in the repo under `data/`.

- titles.json : the watch-list, managed by the user over Telegram.
- state.json  : last-seen TMDB snapshot per title + Telegram bookkeeping.
"""
import json
import os

from . import config


def _titles_path() -> str:
    return os.path.join(config.data_dir(), "titles.json")


def _state_path() -> str:
    return os.path.join(config.data_dir(), "state.json")


def _load(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def _atomic_write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def load_titles() -> dict:
    data = _load(_titles_path(), {"titles": []})
    data.setdefault("titles", [])
    return data


def save_titles(data: dict) -> None:
    _atomic_write(_titles_path(), data)


def load_state() -> dict:
    data = _load(_state_path(), {})
    data.setdefault("telegram_offset", 0)
    data.setdefault("subscribers", [])
    data.setdefault("recent_update_ids", [])
    data.setdefault("titles", {})
    return data


def save_state(data: dict) -> None:
    _atomic_write(_state_path(), data)


def key_for(media_type: str, tmdb_id) -> str:
    return f"{media_type}:{tmdb_id}"
