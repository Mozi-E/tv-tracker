"""Gather and rank candidates for the weekly show recommendation.

This is the I/O half (TMDB calls); the scoring lives in `recommend.py`.
`recommend_for_user` is called both by the weekly job (run.py) and by the
on-demand `/rec` command (commands.py).
"""
import html as _html
from datetime import date

from . import recommend, tmdb
from .diff import tv_snapshot

MAX_SOURCE_SHOWS = 10   # cap the /recommendations fan-out
SHORTLIST = 5           # how many candidates get full detail lookups


def recommend_for_user(tracked_tv_ids, state_titles, exclude_ids, today=None):
    """
    tracked_tv_ids : list[int]  the user's tracked show ids
    state_titles   : dict       state["titles"], the shared snapshot cache
    exclude_ids    : set[int]   never suggest these (tracked + declined + past recs)
    -> (html_message, picked_id)  or  (None, None)
    """
    today = today or date.today().isoformat()

    snaps = [state_titles[f"tv:{i}"] for i in tracked_tv_ids if f"tv:{i}" in state_titles]
    profile = recommend.build_profile(snaps)
    if not profile["genres"]:
        return None, None  # nothing to go on yet (snapshots not built, or genreless)

    try:
        genre_map = tmdb.tv_genre_map()
    except tmdb.TMDBError:
        genre_map = {}

    pool = {}
    for tv_id in tracked_tv_ids[:MAX_SOURCE_SHOWS]:
        try:
            for c in tmdb.tv_recommendations(tv_id):
                if c.get("id"):
                    pool[c["id"]] = c
        except tmdb.TMDBError:
            pass

    top_ids = _top_genre_ids(profile, genre_map)
    if top_ids:
        try:
            for c in tmdb.discover_tv({
                "sort_by": "popularity.desc",
                "with_genres": ",".join(str(g) for g in top_ids),
                "first_air_date.gte": today,
                "include_adult": "false",
            }):
                if c.get("id"):
                    pool.setdefault(c["id"], c)
        except tmdb.TMDBError:
            pass

    pool = {i: c for i, c in pool.items() if i not in exclude_ids}
    short = recommend.shortlist(pool.values(), profile, genre_map, exclude_ids, today, SHORTLIST)
    if not short:
        return None, None

    enriched = []
    for c in short:
        try:
            d = tmdb.tv_details(c["id"])
        except tmdb.TMDBError:
            continue
        try:
            kw = tmdb.tv_keywords(c["id"])
        except tmdb.TMDBError:
            kw = []
        snap = tv_snapshot(d, keywords=kw)
        enriched.append({
            "id": c["id"],
            "name": snap["name"],
            "genres": snap["genres"],
            "keywords": snap["keywords"],
            "networks": snap["networks"],
            "vote_average": snap["vote_average"],
            "popularity": c.get("popularity") or d.get("popularity") or 0,
            "first_air_date": snap["first_air_date"] or c.get("first_air_date"),
        })

    pick = recommend.best(enriched, profile, today)
    if not pick:
        return None, None
    return _format(pick, profile), pick["id"]


def _top_genre_ids(profile, genre_map, n=3):
    name_to_id = {v: k for k, v in genre_map.items()}
    return [name_to_id[g] for g, _ in profile["genres"].most_common(n) if g in name_to_id]


def _format(pick, profile):
    link = tmdb.web_url("tv", pick["id"])
    lines = [
        f'\U0001f3ac Weekly pick: <a href="{link}">'
        f'{_html.escape(pick["name"] or "a new show")}</a>'
    ]
    meta = []
    if pick.get("first_air_date"):
        meta.append(f"premieres {pick['first_air_date']}")
    if pick.get("networks"):
        meta.append(pick["networks"][0])
    if meta:
        lines.append(_html.escape(" · ".join(meta)))
    lines.append(_html.escape(recommend.describe_match(pick, profile)))

    try:
        il = (tmdb.watch_providers("tv", pick["id"]).get("results") or {}).get("IL") or {}
        names = [p["provider_name"] for p in il.get("flatrate", [])][:4]
        if names:
            lines.append("Watch in Israel: " + _html.escape(", ".join(names)))
        elif il.get("link"):
            lines.append(f'<a href="{il["link"]}">Israeli availability on TMDB</a>')
    except tmdb.TMDBError:
        pass

    return "\n".join(lines)
