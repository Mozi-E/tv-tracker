"""Pure functions: reduce TMDB payloads to a small snapshot, then diff snapshots.

No network here, so this module is fully unit-testable (see selftest.py).
A snapshot is what we persist in state.json; a diff turns
(old snapshot, new snapshot) into a list of human-readable change lines.
The very first observation of a title returns no changes (baseline only).
"""
from datetime import date


def _today() -> str:
    return date.today().isoformat()


# --------------------------------------------------------------------------- TV

def tv_snapshot(details: dict) -> dict:
    """Reduce a TMDB /tv/{id} response."""
    seasons = {}
    for s in details.get("seasons") or []:
        num = s.get("season_number")
        if num is None or num == 0:  # skip "Specials"
            continue
        seasons[str(num)] = s.get("air_date") or None
    nxt = details.get("next_episode_to_air") or {}
    return {
        "kind": "tv",
        "name": details.get("name"),
        "status": details.get("status"),
        "number_of_seasons": details.get("number_of_seasons"),
        "seasons": seasons,  # {"1": "2011-04-17", "2": None, ...}
        "next_episode_air_date": nxt.get("air_date"),
    }


def diff_tv(old, new, today=None):
    today = today or _today()
    if not old:
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
