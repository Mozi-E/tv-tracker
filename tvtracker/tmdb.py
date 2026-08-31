"""Minimal TMDB API v3 client (uses the `api_key` query-param auth)."""
from . import config


class TMDBError(RuntimeError):
    pass


def _get(path: str, **params):
    import requests  # lazy: keeps offline tests dependency-free

    key = config.tmdb_api_key()
    if not key:
        raise TMDBError("TMDB_API_KEY is not set")
    params["api_key"] = key
    resp = requests.get(f"{config.TMDB_BASE}{path}", params=params, timeout=30)
    if resp.status_code != 200:
        raise TMDBError(f"GET {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def search(query: str, limit: int = 5):
    """Search TV shows and movies. Returns a list sorted by popularity."""
    data = _get("/search/multi", query=query, include_adult="false", page=1)
    results = []
    for r in data.get("results", []):
        mt = r.get("media_type")
        if mt not in ("tv", "movie"):
            continue
        title = r.get("name") if mt == "tv" else r.get("title")
        date = r.get("first_air_date") if mt == "tv" else r.get("release_date")
        results.append(
            {
                "id": r["id"],
                "media_type": mt,
                "title": title or "(untitled)",
                "year": (date or "")[:4],
                "popularity": r.get("popularity", 0) or 0,
            }
        )
    results.sort(key=lambda x: x["popularity"], reverse=True)
    return results[:limit]


def tv_details(tv_id: int):
    return _get(f"/tv/{tv_id}")


def movie_details(movie_id: int):
    return _get(f"/movie/{movie_id}")


def collection_details(collection_id: int):
    return _get(f"/collection/{collection_id}")
