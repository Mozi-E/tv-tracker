#!/usr/bin/env python3
"""Offline end-to-end test of tvtracker.run.main().

Stubs the TMDB and Telegram network layers, then runs the full daily cycle
twice to prove: commands mutate titles.json, the first check only sets a
baseline (no alert), and a later TMDB change produces exactly one alert
that is persisted so it is not repeated.

    python3 itest.py
"""
import json
import os
import sys
import tempfile

DATA = tempfile.mkdtemp(prefix="tvt-it-")
os.environ["TV_TRACKER_DATA_DIR"] = DATA

from tvtracker import run, telegram, tmdb

sent = []           # (chat_id, text) actually "sent" to Telegram
pending_updates = []  # what getUpdates will return next call


def stub_get_updates(offset=0, timeout=0):
    global pending_updates
    out = [u for u in pending_updates if u["update_id"] >= offset]
    pending_updates = []
    return out


def stub_send_message(chat_id, text):
    sent.append((chat_id, text))
    return {"message_id": len(sent)}


telegram.get_updates = stub_get_updates
telegram.send_message = stub_send_message

# --- fake TMDB world -------------------------------------------------------
TV = {
    1399: {
        "name": "Game of Thrones",
        "status": "Returning Series",
        "number_of_seasons": 2,
        "seasons": [
            {"season_number": 0, "air_date": "2010-01-01"},
            {"season_number": 1, "air_date": "2011-04-17"},
            {"season_number": 2, "air_date": "2012-04-01"},
        ],
        "next_episode_to_air": None,
    }
}
tmdb.tv_details = lambda i: TV[i]
tmdb.movie_details = lambda i: (_ for _ in ()).throw(AssertionError("no movie expected"))
tmdb.search = lambda q, limit=5: [
    {"id": 1399, "media_type": "tv", "title": "Game of Thrones", "year": "2011", "popularity": 99}
]


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


fails = 0


def check(label, cond):
    global fails
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails += 1


print("Run 1: user sends /start then /add via Telegram")
pending_updates = [
    {"update_id": 10, "message": {"text": "/start", "chat": {"id": 777}}},
    {"update_id": 11, "message": {"text": "/add game of thrones", "chat": {"id": 777}}},
]
run.main([])
check("welcome + add confirmation sent", len(sent) == 2 and "Now tracking" in sent[1][1])
check("title written to titles.json", load("titles.json")["titles"][0]["id"] == 1399)
check("baseline stored, no alert", "tv:1399" in load("state.json")["titles"])
check("no false 'new season' alert on first sight",
      not any("New season" in t for _, t in sent))
check("offset advanced past processed updates", load("state.json")["telegram_offset"] == 12)

print("Run 2: TMDB now shows a newly announced Season 3, no new Telegram messages")
sent.clear()
TV[1399] = dict(TV[1399],
                number_of_seasons=3,
                seasons=TV[1399]["seasons"] + [{"season_number": 3, "air_date": None}])
run.main([])
check("exactly one alert delivered", len(sent) == 1)
check("alert is about Season 3", "Season 3" in sent[0][1] and "Game of Thrones" in sent[0][1])
check("alert went to the subscriber", sent[0][0] == 777)
check("new snapshot persisted", "3" in load("state.json")["titles"]["tv:1399"]["seasons"])

print("Run 3: nothing changed at all")
sent.clear()
run.main([])
check("no duplicate alert", sent == [])

print(f"\n{'FAILED' if fails else 'PASSED'} ({fails} failing)")
sys.exit(1 if fails else 0)
