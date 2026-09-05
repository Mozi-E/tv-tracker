"""Load/save the JSON files that live in the repo under `data/`.

- titles.json : one watch-list per Telegram user, managed over Telegram.
- state.json  : last-seen TMDB snapshot per title (shared - the same movie or
                show tracked by two people is only fetched from TMDB once) +
                Telegram bookkeeping (subscribers, admins, ...).
"""
import json
import os

from . import config


def _titles_path() -> str:
    return os.path.join(config.data_dir(), "titles.json")


def _state_path() -> str:
    return os.path.join(config.data_dir(), "state.json")


def _maintenance_path() -> str:
    return os.path.join(config.data_dir(), "maintenance.json")


def _invites_path() -> str:
    return os.path.join(config.data_dir(), "invites.json")


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
    data = _load(_titles_path(), {"users": {}})
    if "titles" in data and "users" not in data:
        # one-time migration from the old shared flat-list format: each
        # entry's 'added_by' becomes that user's own list.
        migrated = {"users": {}}
        for t in data.get("titles", []):
            owner = str(t.get("added_by", "unknown"))
            entry = {"id": t["id"], "media_type": t["media_type"], "title": t["title"]}
            migrated["users"].setdefault(owner, {"titles": []})["titles"].append(entry)
        data = migrated
    data.setdefault("users", {})
    return data


def save_titles(data: dict) -> None:
    _atomic_write(_titles_path(), data)


def user_titles(titles_data: dict, chat_id) -> list:
    """The mutable list of titles a specific user tracks."""
    users = titles_data.setdefault("users", {})
    return users.setdefault(str(chat_id), {}).setdefault("titles", [])


def all_titles_by_key(titles_data: dict):
    """Dedupe every user's titles by (media_type, id).

    Returns (by_key, owners): by_key maps a key to one representative title
    dict (for its id/media_type/title); owners maps the same key to the list
    of chat ids currently tracking it. A title tracked by two users is only
    ever checked against TMDB once, but each owner is notified separately.
    """
    by_key, owners = {}, {}
    for uid_str, udata in titles_data.get("users", {}).items():
        try:
            uid = int(uid_str)
        except (TypeError, ValueError):
            continue
        for t in (udata or {}).get("titles", []):
            k = key_for(t["media_type"], t["id"])
            by_key.setdefault(k, t)
            owners.setdefault(k, []).append(uid)
    return by_key, owners


def load_state() -> dict:
    data = _load(_state_path(), {})
    data.setdefault("telegram_offset", 0)
    data.setdefault("subscribers", [])
    data.setdefault("admins", [])
    data.setdefault("bot_username", None)
    data.setdefault("recent_update_ids", [])
    data.setdefault("titles", {})
    return data


def save_state(data: dict) -> None:
    _atomic_write(_state_path(), data)


def load_maintenance() -> dict:
    data = _load(_maintenance_path(), {"tasks": []})
    data.setdefault("tasks", [])
    return data


def save_maintenance(data: dict) -> None:
    _atomic_write(_maintenance_path(), data)


def load_invites() -> dict:
    data = _load(_invites_path(), {"invites": {}})
    data.setdefault("invites", {})
    return data


def save_invites(data: dict) -> None:
    _atomic_write(_invites_path(), data)


def key_for(media_type: str, tmdb_id) -> str:
    return f"{media_type}:{tmdb_id}"
