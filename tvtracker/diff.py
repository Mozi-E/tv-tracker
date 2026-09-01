"""Pure functions: reduce TMDB payloads to a small snapshot, then diff snapshots.

No network here, so this module is fully unit-testable (see selftest.py).
A snapshot is what we persist in state.json; a diff turns
(old snapshot, new snapshot) into a list of human-readable change lines.
The very first observation of a title returns no changes (baseline only).
"""
from datetime import date

# How many days after an episode's air date we'll still send a "new episode"
# alert (covers the daily run being delayed or a missed run or two).
EPISODE_ALERT_WINDOW_DAYS = 3


def _today() -> str:
    return date.today().isoformat()


def _within_days(air_date: str, today: str, n: int) -> bool:
    try:
        aired = date.fromisoformat(air_date)
        now = date.fromisoformat(today)
    except (TypeError, ValueError):
        return False
    return 0 <= (now - aired).days <= n


# --------------------------------------------------------------------------- TV

def _episode(ep: dict):
    """Reduce a TMDB last/next_episode_to_air object."""
    if not ep:
        return None
    s, n = ep.get("season_number"), ep.get("episode_number")
    if s is None or n is None:
        return None
    return {
        "key": f"S{int(s):02d}E{int(n):02d}",
        "name": ep.get("name") or None,
        "air_date": ep.get("air_date") or None,
    }


def _current_episode_keys(snap: dict, aired_only: bool = False):
    eps = [snap.get("last_episode")]
    if not aired_only:
        eps.append(snap.get("next_episode"))
    return [ep["key"] for ep in eps if ep and ep.get("key")]


def tv_snapshot(details: dict) -> dict:
    """Reduce a TMDB /tv/{id} response."""
    seasons = {}
    for s in details.get("seasons") or []:
        num = s.get("season_number")
        if num is None or num == 0:  # skip "Specials"
            continue
        seasons[str(num)] = s.get("air_date") or None
    return {
        "kind": "tv",
        "name": details.get("name"),
        "status": details.get("status"),
        "number_of_seasons": details.get("number_of_seasons"),
        "seasons": seasons,  # {"1": "2011-04-17", "2": None, ...}
        "last_episode": _episode(details.get("last_episode_to_air")),
        "next_episode": _episode(details.get("next_episode_to_air")),
        "notified_episodes": [],  # episode keys we've already alerted on
    }


def diff_tv(old, new, today=None):
    today = today or _today()

    if not old:
        # baseline: remember only the already-aired episode, so the next one
        # still triggers an alert on the day it airs
        new["notified_episodes"] = _current_episode_keys(new, aired_only=True)
        return []

    changes = []
    old_seasons = old.get("seasons", {})
    new_seasons = new.get("seasons", {})

    for num in sorted(new_seasons, key=lambda x: int(x)):
        new_air = new_seasons[num]
        if num not in old_seasons:
            if not new_air:
                changes.append(f"New season announced: Season {num} (no air date yet)")
            elif new_air > today:
                changes.append(f"New season coming: Season {num} — premieres {new_air}")
            else:
                changes.append(f"New season is out: Season {num} (aired {new_air})")
        else:
            old_air = old_seasons[num]
            if not old_air and new_air:
                if new_air > today:
                    changes.append(f"Season {num} got a premiere date: {new_air}")
                else:
                    changes.append(f"Season {num} is now out (aired {new_air})")

    if old.get("status") != new.get("status") and new.get("status") == "Returning Series" \
            and old.get("status") in ("Ended", "Canceled"):
        changes.append(f"Show status: {old.get('status')} -> Returning Series")

    # --- new episode, on/around its air date ---
    if "notified_episodes" not in old:
        # first run after this feature shipped: seed, don't backfill
        notified = _current_episode_keys(new, aired_only=True)
    else:
        notified = list(old.get("notified_episodes", []))
        for ep in (new.get("next_episode"), new.get("last_episode")):
            if not ep or not ep.get("key"):
                continue
            ad = ep.get("air_date")
            if not ad or ad > today:
                continue  # not out yet
            if ep["key"] in notified:
                continue
            if not _within_days(ad, today, EPISODE_ALERT_WINDOW_DAYS):
                continue  # too old to be worth an alert
            name = ep.get("name") or "new episode"
            when = "airs today" if ad == today else f"aired {ad}"
            changes.append(f'New episode {ep["key"]}: "{name}" - {when}')
            notified.append(ep["key"])
    new["notified_episodes"] = notified[-20:]

    return changes


# ------------------------------------------------------------------------ movie

def movie_snapshot(movie_details: dict, collection_details=None) -> dict:
    collection = movie_details.get("belongs_to_collection")
    parts = {}
    if collection_details:
        for p in collection_details.get("parts") or []:
            parts[str(p["id"])] = {
                "title": p.get("title") or "(untitled)",
                "release_date": p.get("release_date") or None,
            }
    return {
        "kind": "movie",
        "title": movie_details.get("title"),
        "collection_id": collection.get("id") if collection else None,
        "collection_name": collection.get("name") if collection else None,
        "parts": parts,  # {"603": {"title": "The Matrix", "release_date": "1999-03-30"}}
    }


def diff_movie(old, new, today=None):
    today = today or _today()
    if not old:
        return []
    changes = []
    old_parts = old.get("parts", {})
    new_parts = new.get("parts", {})

    for pid, info in new_parts.items():
        title = info.get("title") or "(untitled)"
        rd = info.get("release_date")
        if pid not in old_parts:
            if not rd:
                changes.append(f'New film in the franchise: "{title}" (no release date yet)')
            elif rd > today:
                changes.append(f'Sequel / related film announced: "{title}" — releases {rd}')
            else:
                changes.append(f'New franchise film is out: "{title}" (released {rd})')
        else:
            old_rd = old_parts[pid].get("release_date")
            if not old_rd and rd:
                if rd > today:
                    changes.append(f'"{title}" got a release date: {rd}')
                else:
                    changes.append(f'"{title}" is now out (released {rd})')

    return changes
