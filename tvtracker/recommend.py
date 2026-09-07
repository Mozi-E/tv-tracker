"""Weekly show recommendation - pure logic, no network.

`build_profile` turns the snapshots of the shows a user tracks into weighted
genre / keyword / network counts. `coarse_score` ranks raw candidate objects
(from /discover or /recommendations, which only carry genre_ids) so the run
step only has to fetch full details for a short list. `fine_score` re-ranks
that short list once genres/keywords/networks names are known. `describe_match`
builds the "why this fits you" line.

The run step (tvtracker/run.py:run_recommendations) owns all the I/O.
"""
import math
from collections import Counter
from datetime import date, timedelta

# how far ahead a premiere still counts as "coming soon"
SOON_DAYS = 150


def _today() -> str:
    return date.today().isoformat()


def iso_week(d: date = None) -> str:
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def build_profile(snapshots) -> dict:
    genres, keywords, networks = Counter(), Counter(), Counter()
    ratings = []
    for s in snapshots:
        for g in s.get("genres") or []:
            genres[g] += 1
        for k in s.get("keywords") or []:
            keywords[k] += 1
        for n in s.get("networks") or []:
            networks[n] += 1
        if s.get("vote_average"):
            ratings.append(s["vote_average"])
    return {
        "genres": genres,
        "keywords": keywords,
        "networks": networks,
        "avg_rating": (sum(ratings) / len(ratings)) if ratings else None,
        "count": len(snapshots),
    }


def _is_soon(first_air_date: str, today: str) -> bool:
    if not first_air_date or first_air_date <= today:
        return False
    try:
        horizon = (date.fromisoformat(today) + timedelta(days=SOON_DAYS)).isoformat()
    except ValueError:
        return False
    return first_air_date <= horizon


def coarse_score(cand: dict, profile: dict, genre_map: dict) -> float:
    """Rank a raw TMDB candidate using only what /discover returns."""
    names = [genre_map.get(gid) for gid in cand.get("genre_ids", [])]
    g_overlap = sum(profile["genres"].get(n, 0) for n in names if n)
    score = 3.0 * g_overlap
    if profile["avg_rating"] and cand.get("vote_average"):
        score -= 0.8 * abs(profile["avg_rating"] - cand["vote_average"])
    score += 0.3 * math.log1p(cand.get("popularity") or 0)
    return score


def fine_score(cand: dict, profile: dict) -> float:
    """Re-rank once the candidate's genre/keyword/network names are known.
    `cand` here carries 'genres', 'keywords', 'networks' as name lists."""
    g = sum(profile["genres"].get(x, 0) for x in cand.get("genres", []))
    k = sum(profile["keywords"].get(x, 0) for x in cand.get("keywords", []))
    n = sum(profile["networks"].get(x, 0) for x in cand.get("networks", []))
    score = 3.0 * g + 2.0 * k + 1.5 * n
    if profile["avg_rating"] and cand.get("vote_average"):
        score -= 0.8 * abs(profile["avg_rating"] - cand["vote_average"])
    score += 0.3 * math.log1p(cand.get("popularity") or 0)
    return score


def shortlist(candidates, profile, genre_map, exclude_ids, today=None, limit=5):
    """Coarse-rank upcoming, non-excluded candidates; return the top `limit`."""
    today = today or _today()
    scored = []
    for c in candidates:
        if c.get("id") in exclude_ids:
            continue
        if not _is_soon(c.get("first_air_date"), today):
            continue
        scored.append((coarse_score(c, profile, genre_map), c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:limit]]


def best(candidates_with_names, profile, today=None):
    """Pick the single highest fine-scored candidate, or None.
    Each item must carry id/name/genres/keywords/networks/first_air_date/... ."""
    today = today or _today()
    ranked = []
    for c in candidates_with_names:
        if not _is_soon(c.get("first_air_date"), today):
            continue
        s = fine_score(c, profile)
        if s > 0:
            ranked.append((s, c))
    if not ranked:
        return None
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1]


def describe_match(cand: dict, profile: dict) -> str:
    matched_g = [g for g in cand.get("genres", []) if g in profile["genres"]]
    matched_k = [k for k in cand.get("keywords", []) if k in profile["keywords"]][:3]
    parts = []
    if matched_g:
        parts.append(", ".join(matched_g[:3]))
    if matched_k:
        parts.append(", ".join(matched_k))
    if not parts:
        return "picked from shows similar to the ones you track"
    return "shares " + " and ".join(parts) + " with shows you track"
