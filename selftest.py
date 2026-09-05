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

from datetime import date, timedelta

from tvtracker import commands, maintenance, store, telegram, tmdb
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
        "last_episode_to_air": {"season_number": 2, "episode_number": 3,
                                "air_date": "2012-04-08", "name": "What Is Dead May Never Die"},
        "next_episode_to_air": {"season_number": 2, "episode_number": 4,
                                "air_date": "2012-04-15", "name": "Garden of Bones"},
    }
    snap = tv_snapshot(got)
    check("specials dropped", set(snap["seasons"]) == {"1", "2"})
    check("episode keys parsed",
          snap["last_episode"]["key"] == "S02E03" and snap["next_episode"]["key"] == "S02E04")

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

    # --- new-episode alerts ---
    import copy

    base = tv_snapshot(got)
    diff_tv(None, base)  # establish baseline
    check("baseline seeds only the aired episode", base["notified_episodes"] == ["S02E03"])

    TODAY = "2012-04-15"
    aired = copy.deepcopy(base)
    aired["last_episode"] = {"key": "S02E04", "name": "Garden of Bones", "air_date": TODAY}
    aired["next_episode"] = {"key": "S02E05", "name": "The Ghost of Harrenhal", "air_date": "2012-04-22"}
    d = diff_tv(copy.deepcopy(base), aired, today=TODAY)
    check("new episode alert on air day",
          d == ['New episode S02E04: "Garden of Bones" - airs today'])
    check("aired episode is remembered", aired["notified_episodes"] == ["S02E03", "S02E04"])

    d = diff_tv(aired, copy.deepcopy(aired), today="2012-04-16")
    check("no repeat alert next run", d == [])

    old_ep = copy.deepcopy(base)
    stale = copy.deepcopy(base)
    stale["last_episode"] = {"key": "S02E09", "name": "Old", "air_date": "2012-01-01"}
    stale["next_episode"] = {"key": "S02E10", "name": "Later", "air_date": "2099-01-01"}
    check("episode outside the alert window -> no alert",
          diff_tv(old_ep, stale, today=TODAY) == [])

    pre_feature = {"seasons": base["seasons"], "status": "Returning Series", "number_of_seasons": 2}
    check("pre-feature snapshot seeds instead of backfilling",
          diff_tv(pre_feature, copy.deepcopy(aired), today=TODAY) == [])


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
    store.user_titles(titles, 1).append({"id": 1399, "media_type": "tv", "title": "GoT"})
    store.save_titles(titles)
    check("titles persisted", store.load_titles()["users"]["1"]["titles"][0]["id"] == 1399)

    old_flat = {"titles": [{"id": 603, "media_type": "movie", "title": "The Matrix", "added_by": 42}]}
    store.save_titles(old_flat)
    migrated = store.load_titles()
    check("old flat format migrates to per-user",
          migrated["users"]["42"]["titles"] == [{"id": 603, "media_type": "movie", "title": "The Matrix"}])

    by_key, owners = store.all_titles_by_key({"users": {
        "1": {"titles": [{"id": 1399, "media_type": "tv", "title": "GoT"}]},
        "2": {"titles": [{"id": 1399, "media_type": "tv", "title": "GoT"}]},
    }})
    check("shared title deduped to one entry", list(by_key) == ["tv:1399"])
    check("both owners recorded", sorted(owners["tv:1399"]) == [1, 2])

    st = store.load_state()
    st["telegram_offset"] = 42
    st["subscribers"].append(555)
    store.save_state(st)
    reloaded = store.load_state()
    check("state persisted", reloaded["telegram_offset"] == 42 and 555 in reloaded["subscribers"])
    # reset for the command tests below
    store.save_titles({"users": {}})
    store.save_state({"telegram_offset": 0, "subscribers": [], "admins": [], "titles": {}})


# --------------------------------------------------------- command handler
def _update(text, chat_id=555):
    return {"update_id": 1, "message": {"text": text, "chat": {"id": chat_id}}}


def _fresh_state():
    return {"telegram_offset": 0, "subscribers": [], "admins": [], "recent_update_ids": [], "titles": {}}


def test_commands():
    print("Telegram command handler")
    titles = {"users": {}}
    state = _fresh_state()
    invites = {"invites": {}}
    my_titles = lambda: store.user_titles(titles, 555)

    r = commands.handle_update(_update("/help"), titles, state, invites)
    check("first-ever message bootstraps sender as admin", r and "first user" in r[0][1])
    check("sender auto-subscribed", 555 in state["subscribers"])
    check("sender made admin", 555 in state["admins"])

    r = commands.handle_update(_update("/help"), titles, state, invites)
    check("/help replies", r and "Tracker" in r[0][1])

    r = commands.handle_update(_update("/list"), titles, state, invites)
    check("/list empty", "not tracking anything" in r[0][1])

    # stub TMDB: single strong match
    tmdb.search = lambda q, limit=5: [
        {"id": 603, "media_type": "movie", "title": "The Matrix", "year": "1999", "popularity": 90}
    ]
    r = commands.handle_update(_update("/add the matrix"), titles, state, invites)
    check("/add single match adds it", any(t["id"] == 603 for t in my_titles()))
    check("/add confirms", "Now tracking" in r[0][1])

    r = commands.handle_update(_update("/add the matrix"), titles, state, invites)
    check("/add duplicate rejected", "Already tracking" in r[0][1])

    # stub TMDB: multiple matches -> disambiguation
    tmdb.search = lambda q, limit=5: [
        {"id": 1, "media_type": "tv", "title": "Dune: Prophecy", "year": "2024", "popularity": 50},
        {"id": 2, "media_type": "movie", "title": "Dune", "year": "2021", "popularity": 80},
    ]
    r = commands.handle_update(_update("/add dune"), titles, state, invites)
    check("/add multi -> choices listed", "/add movie 2" in r[0][1] and "/add tv 1" in r[0][1])
    check("/add multi -> nothing added yet", all(t["id"] not in (1, 2) for t in my_titles()))

    # explicit id form
    tmdb.tv_details = lambda i: {"name": "Dune: Prophecy", "seasons": [], "status": "Returning Series", "number_of_seasons": 1}
    r = commands.handle_update(_update("/add tv 1"), titles, state, invites)
    check("/add tv <id> adds it", any(t["id"] == 1 and t["media_type"] == "tv" for t in my_titles()))

    r = commands.handle_update(_update("/list"), titles, state, invites)
    check("/list shows two items", r[0][1].count("/remove") == 2)
    check("/list items are TMDB hyperlinks",
          '<a href="https://www.themoviedb.org/' in r[0][1] and r[0][2] == "HTML")

    r = commands.handle_update(_update("/remove 1"), titles, state, invites)
    check("/remove by number works", len(my_titles()) == 1 and "Stopped tracking" in r[0][1])

    r = commands.handle_update(_update("/remove 9"), titles, state, invites)
    check("/remove out of range handled", "no item #9" in r[0][1])

    r = commands.handle_update(_update("hello there"), titles, state, invites)
    check("non-command nudges to /help", "/help" in r[0][1])


def test_invites():
    print("Invite links")
    titles = {"users": {}}
    state = _fresh_state()
    invites = {"invites": {}}

    # bootstrap the admin
    commands.handle_update(_update("/start", chat_id=1), titles, state, invites)
    check("bootstrap admin", state["admins"] == [1])

    r = commands.handle_update(_update("hello", chat_id=2), titles, state, invites)
    check("stranger blocked before any invite exists", "invite-only" in r[0][1])
    check("stranger not subscribed", 2 not in state["subscribers"])

    telegram.get_me = lambda: {"username": "elior_tvtracker_bot"}
    r = commands.handle_update(_update("/invite", chat_id=1), titles, state, invites)
    check("admin creates a default invite", "t.me/elior_tvtracker_bot?start=" in r[0][1])
    token = r[0][1].split("start=")[1].split()[0]
    check("invite stored with defaults", invites["invites"][token]["max_uses"] == 1)

    r = commands.handle_update(_update(f"/start {token}", chat_id=2), titles, state, invites)
    check("valid invite grants access", 2 in state["subscribers"])
    check("welcome message sent", "You're in" in r[0][1])

    r = commands.handle_update(_update("/invite", chat_id=2), titles, state, invites)
    check("subscribed non-admin can't create invites", "Only an admin" in r[0][1])

    r = commands.handle_update(_update(f"/start {token}", chat_id=3), titles, state, invites)
    check("exhausted single-use invite rejected", "used up" in r[0][1])
    check("third stranger not subscribed", 3 not in state["subscribers"])

    r = commands.handle_update(_update("/start not-a-real-token", chat_id=4), titles, state, invites)
    check("bogus token rejected", "invalid" in r[0][1])

    r = commands.handle_update(_update("/invite 3 30", chat_id=1), titles, state, invites)
    token2 = r[0][1].split("start=")[1].split()[0]
    check("custom uses/days honoured", invites["invites"][token2]["max_uses"] == 3)
    check("custom expiry honoured",
          invites["invites"][token2]["expires"] == (date.today() + timedelta(days=30)).isoformat())

    expired_token = "expired1"
    invites["invites"][expired_token] = {"max_uses": 1, "uses": 0, "expires": "2000-01-01"}
    r = commands.handle_update(_update(f"/start {expired_token}", chat_id=5), titles, state, invites)
    check("expired invite rejected", "expired" in r[0][1])
    check("expired-invite user not subscribed", 5 not in state["subscribers"])


def test_url_parsing_and_add():
    print("TMDB URL parsing + /add by link")
    check("plain tv url", tmdb.parse_tmdb_url("https://www.themoviedb.org/tv/1399") == ("tv", 1399))
    check("slug tv url",
          tmdb.parse_tmdb_url("https://www.themoviedb.org/tv/1399-game-of-thrones") == ("tv", 1399))
    check("movie url with query",
          tmdb.parse_tmdb_url("https://themoviedb.org/movie/603-the-matrix?language=en-US") == ("movie", 603))
    check("season deep link",
          tmdb.parse_tmdb_url("www.themoviedb.org/tv/1399/season/2") == ("tv", 1399))
    check("non-tmdb url -> None", tmdb.parse_tmdb_url("https://example.com/tv/1") is None)
    check("plain text -> None", tmdb.parse_tmdb_url("game of thrones") is None)

    titles = {"users": {}}
    state = _fresh_state()
    state["subscribers"] = [555]  # pre-approved, so /add isn't swallowed by bootstrap
    state["admins"] = [555]
    invites = {"invites": {}}
    tmdb.tv_details = lambda i: {"name": "Game of Thrones", "seasons": [],
                                "status": "Ended", "number_of_seasons": 8}
    r = commands.handle_update(
        _update("/add https://www.themoviedb.org/tv/1399-game-of-thrones"), titles, state, invites
    )
    check("/add <url> tracks the right id",
          store.user_titles(titles, 555) == [{"id": 1399, "media_type": "tv",
                                              "title": "Game of Thrones"}])
    check("/add <url> confirmation links to TMDB",
          '<a href="https://www.themoviedb.org/tv/1399"' in r[0][1] and r[0][2] == "HTML")


def test_maintenance():
    print("Maintenance reminders")

    def task(due):
        return {"id": "pat", "title": "Renew PAT", "due": due,
                "notes": "do the thing", "notified": [], "due_seen": None}

    T = date(2026, 11, 1)

    t = task("2026-12-01")  # 30 days out
    check("nothing at 30 days", maintenance.scan([t], date(2026, 11, 1)) == [])

    pend = maintenance.scan([t], date(2026, 11, 12))  # 19 days out
    check("fires at <=20 days", len(pend) == 1 and "in 19 days" in pend[0][2])
    maintenance.mark(t, pend[0][1])
    check("20-milestone recorded", "20" in t["notified"])
    check("no re-fire at 15 days", maintenance.scan([t], date(2026, 11, 16)) == [])

    pend = maintenance.scan([t], date(2026, 11, 22))  # 9 days
    check("fires at <=10 days", len(pend) == 1 and pend[0][1] == "10")
    maintenance.mark(t, "10")

    pend = maintenance.scan([t], date(2026, 11, 30))  # 1 day
    check("fires at 1 day ('due tomorrow')", pend and "due tomorrow" in pend[0][2])
    maintenance.mark(t, "1")
    check("all milestones done", set(t["notified"]) == {"1", "10", "20"})

    pend = maintenance.scan([t], date(2026, 12, 5))  # overdue
    check("overdue fires once", pend and pend[0][1] == "overdue" and "OVERDUE" in pend[0][2])
    maintenance.mark(t, "overdue")
    check("overdue not repeated", maintenance.scan([t], date(2026, 12, 6)) == [])

    t["due"] = "2027-06-01"  # user pushed the date out
    check("editing due re-arms", maintenance.scan([t], date(2027, 5, 20)) != [])
    check("re-arm cleared old milestones", "overdue" not in t["notified"])

    bad = {"id": "x", "title": "no date", "due": None}
    check("missing due is skipped", maintenance.scan([bad], T) == [])


if __name__ == "__main__":
    test_tv_snapshot_and_diff()
    test_movie_snapshot_and_diff()
    test_store_roundtrip()
    test_commands()
    test_invites()
    test_url_parsing_and_add()
    test_maintenance()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
