#!/usr/bin/env python3
"""Offline self-test. No network, no third-party packages.

    python3 selftest.py

Exercises the diff logic, the snapshot reducers, the JSON store round-trip,
and the Telegram command handler (with TMDB calls stubbed out).
"""
import os
import sys
import tempfile

os.environ.setdefault("TV_TRACKER_DATA_DIR", tempfile.mkdtemp(prefix="tvt-"))

from tvtracker import commands, store, tmdb
from tvtracker.diff import diff_movie, diff_tv, movie_snapshot, tv_snapshot

PASS = 0
FAIL = 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


# --------------------------------------------------------------- TV snapshots
def test_tv_snapshot_and_diff():
    print("TV snapshot + diff")
    got = {
        "seasons": [
            {"season_number": 0, "air_date": "2010-01-01"},   # special -> ignored
            {"season_number": 1, "air_date": "2011-04-17"},
            {"season_number": 2, "air_date": "2012-04-01"},
        ],
        "name": "Game of Thrones",
        "status": "Returning Series",
        "number_of_seasons": 2,
        "next_episode_to_air": {"air_date": "2012-04-08"},
    }
    snap = tv_snapshot(got)
    check("specials dropped", set(snap["seasons"]) == {"1", "2"})

    check("first observation -> no alert", diff_tv(None, snap) == [])
    check("unchanged -> no alert", diff_tv(snap, snap) == [])

    newer = dict(snap, seasons=dict(snap["seasons"], **{"3": None}))
    d = diff_tv(snap, newer)
    check("new season announced (no date)",
          d == ["New season announced: Season 3 (no air date yet)"])

    newer2 = dict(snap, seasons=dict(snap["seasons"], **{"3": "2099-01-01"}))
    d = diff_tv(snap, newer2)
    check("new season with future date",
          d == ["New season coming: Season 3 — premieres 2099-01-01"])

    dated = dict(newer, seasons=dict(newer["seasons"], **{"3": "2099-01-01"}))
    d = diff_tv(newer, dated)
    check("announced -> dated",
          d == ["Season 3 got a premiere date: 2099-01-01"])

    revived_old = dict(snap, status="Ended")
    revived_new = dict(snap, status="Returning Series")
    check("revived show status change",
          diff_tv(revived_old, revived_new) == ["Show status: Ended -> Returning Series"])


# ------------------------------------------------------------ movie snapshots
def test_movie_snapshot_and_diff():
    print("Movie snapshot + diff")
    md = {"title": "The Matrix", "belongs_to_collection": {"id": 2344, "name": "The Matrix Collection"}}
    cd = {"parts": [
        {"id": 603, "title": "The Matrix", "release_date": "1999-03-30"},
        {"id": 604, "title": "The Matrix Reloaded", "release_date": "2003-05-15"},
    ]}
    snap = movie_snapshot(md, cd)
    check("collection id captured", snap["collection_id"] == 2344)
    check("parts captured", set(snap["parts"]) == {"603", "604"})

    check("first observation -> no alert", diff_movie(None, snap) == [])
    check("unchanged -> no alert", diff_movie(snap, snap) == [])

    with_new = dict(snap, parts=dict(snap["parts"], **{"605": {"title": "The Matrix Revolutions", "release_date": None}}))
    d = diff_movie(snap, with_new)
    check("new franchise film, no date",
          d == ['New film in the franchise: "The Matrix Revolutions" (no release date yet)'])

    announced = dict(snap, parts=dict(snap["parts"], **{"606": {"title": "The Matrix Resurrections", "release_date": "2099-12-22"}}))
    d = diff_movie(snap, announced)
    check("sequel announced with future date",
          d == ['Sequel / related film announced: "The Matrix Resurrections" — releases 2099-12-22'])


# ---------------------------------------------------------------- JSON store
def test_store_roundtrip():
    print("Store round-trip")
    titles = store.load_titles()
    titles["titles"].append({"id": 1399, "media_type": "tv", "title": "GoT", "added_by": 1})
    store.save_titles(titles)
    check("titles persisted", store.load_titles()["titles"][0]["id"] == 1399)

    st = store.load_state()
    st["telegram_offset"] = 42
    st["subscribers"].append(555)
    store.save_state(st)
    reloaded = store.load_state()
    check("state persisted", reloaded["telegram_offset"] == 42 and 555 in reloaded["subscribers"])
    # reset for the command tests below
    store.save_titles({"titles": []})
    store.save_state({"telegram_offset": 0, "subscribers": [], "titles": {}})


# --------------------------------------------------------- command handler
def _update(text, chat_id=555):
    return {"update_id": 1, "message": {"text": text, "chat": {"id": chat_id}}}


def test_commands():
    print("Telegram command handler")
    titles = {"titles": []}
    state = {"telegram_offset": 0, "subscribers": [], "titles": {}}

    r = commands.handle_update(_update("/help"), titles, state)
    check("/help replies", r and "Tracker" in r[0][1])
    check("sender auto-subscribed", 555 in state["subscribers"])

    r = commands.handle_update(_update("/list"), titles, state)
    check("/list empty", "not tracking anything" in r[0][1])

    # stub TMDB: single strong match
    tmdb.search = lambda q, limit=5: [
        {"id": 603, "media_type": "movie", "title": "The Matrix", "year": "1999", "popularity": 90}
    ]
    r = commands.handle_update(_update("/add the matrix"), titles, state)
    check("/add single match adds it", any(t["id"] == 603 for t in titles["titles"]))
    check("/add confirms", "Now tracking" in r[0][1])

    r = commands.handle_update(_update("/add the matrix"), titles, state)
    check("/add duplicate rejected", "Already tracking" in r[0][1])

    # stub TMDB: multiple matches -> disambiguation
    tmdb.search = lambda q, limit=5: [
        {"id": 1, "media_type": "tv", "title": "Dune: Prophecy", "year": "2024", "popularity": 50},
        {"id": 2, "media_type": "movie", "title": "Dune", "year": "2021", "popularity": 80},
    ]
    r = commands.handle_update(_update("/add dune"), titles, state)
    check("/add multi -> choices listed", "/add movie 2" in r[0][1] and "/add tv 1" in r[0][1])
    check("/add multi -> nothing added yet", all(t["id"] not in (1, 2) for t in titles["titles"]))

    # explicit id form
    tmdb.tv_details = lambda i: {"name": "Dune: Prophecy", "seasons": [], "status": "Returning Series", "number_of_seasons": 1}
    r = commands.handle_update(_update("/add tv 1"), titles, state)
    check("/add tv <id> adds it", any(t["id"] == 1 and t["media_type"] == "tv" for t in titles["titles"]))

    r = commands.handle_update(_update("/list"), titles, state)
    check("/list shows two items", r[0][1].count("/remove") == 2)

    r = commands.handle_update(_update("/remove 1"), titles, state)
    check("/remove by number works", len(titles["titles"]) == 1 and "Stopped tracking" in r[0][1])

    r = commands.handle_update(_update("/remove 9"), titles, state)
    check("/remove out of range handled", "no item #9" in r[0][1])

    r = commands.handle_update(_update("hello there"), titles, state)
    check("non-command nudges to /help", "/help" in r[0][1])


if __name__ == "__main__":
    test_tv_snapshot_and_diff()
    test_movie_snapshot_and_diff()
    test_store_roundtrip()
    test_commands()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
