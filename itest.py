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


def stub_send_message(chat_id, text, parse_mode=None):
    sent.append((chat_id, text))
    return {"message_id": len(sent)}


telegram.get_updates = stub_get_updates
telegram.send_message = stub_send_message
telegram.get_me = lambda: {"username": "elior_tvtracker_bot"}

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
        "last_episode_to_air": {"season_number": 2, "episode_number": 1,
                                "air_date": "2012-04-01", "name": "The North Remembers"},
        "next_episode_to_air": {"season_number": 2, "episode_number": 2,
                                "air_date": "2099-01-01", "name": "The Night Lands"},
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
check("title written to titles.json", load("titles.json")["users"]["777"]["titles"][0]["id"] == 1399)
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

print("Run 4: webhook mode - update handed in via TELEGRAM_UPDATE_JSON")
sent.clear()
telegram.get_updates = lambda *a, **k: (_ for _ in ()).throw(
    AssertionError("getUpdates must not be called in webhook mode")
)
os.environ["TELEGRAM_WEBHOOK_MODE"] = "1"
os.environ["TELEGRAM_UPDATE_JSON"] = json.dumps(
    {"update_id": 5001, "message": {"text": "/remove 1", "chat": {"id": 777}}}
)
run.main(["--no-check"])
check("webhook: /remove handled without getUpdates", "Stopped tracking" in sent[-1][1])
check("webhook: title removed", load("titles.json")["users"]["777"]["titles"] == [])
check("webhook: update_id remembered", 5001 in load("state.json")["recent_update_ids"])

print("Run 5: webhook mode - Telegram retries the same update")
sent.clear()
run.main(["--no-check"])
check("webhook: duplicate update ignored", sent == [])

print("Run 6: webhook idle - flag set, no update this run")
sent.clear()
del os.environ["TELEGRAM_UPDATE_JSON"]
run.main(["--no-check"])
check("webhook idle: nothing sent, no crash", sent == [])

print("Run 7: re-add the show, then Season 2 Episode 2 airs today")
sent.clear()
del os.environ["TELEGRAM_WEBHOOK_MODE"]
from datetime import date
TODAY = date.today().isoformat()
telegram.get_updates = lambda *a, **k: [
    {"update_id": 7001, "message": {"text": "/add game of thrones", "chat": {"id": 777}}}
]
run.main([])                       # re-adds title, fresh baseline (aired: S02E01)
sent.clear()
telegram.get_updates = lambda *a, **k: []
TV[1399] = dict(
    TV[1399],
    last_episode_to_air={"season_number": 2, "episode_number": 2,
                         "air_date": TODAY, "name": "The Night Lands"},
    next_episode_to_air={"season_number": 2, "episode_number": 3,
                         "air_date": "2099-02-01", "name": "What Is Dead May Never Die"},
)
run.main([])
check("episode alert delivered once", len(sent) == 1)
check("alert names the episode", "S02E02" in sent[0][1] and "airs today" in sent[0][1])
check("episode remembered in state",
      "S02E02" in load("state.json")["titles"]["tv:1399"]["notified_episodes"])

print("Run 8: same episode still latest tomorrow - no repeat")
sent.clear()
run.main([])
check("no repeat episode alert", sent == [])

print("Run 9: admin (777) creates an invite link over webhook")
sent.clear()
os.environ["TELEGRAM_WEBHOOK_MODE"] = "1"
os.environ["TELEGRAM_UPDATE_JSON"] = json.dumps(
    {"update_id": 9001, "message": {"text": "/invite", "chat": {"id": 777}}}
)
run.main(["--no-check"])
check("invite link sent to the admin", sent and "t.me/elior_tvtracker_bot?start=" in sent[0][1])
invite_token = sent[0][1].split("start=")[1].split()[0]
check("invite persisted to invites.json", invite_token in load("invites.json")["invites"])

print("Run 10: a brand-new user redeems the invite over webhook")
sent.clear()
os.environ["TELEGRAM_UPDATE_JSON"] = json.dumps(
    {"update_id": 9002, "message": {"text": f"/start {invite_token}", "chat": {"id": 4242}}}
)
run.main(["--no-check"])
check("new user welcomed", sent and "You're in" in sent[0][1])
check("new user now a subscriber", 4242 in load("state.json")["subscribers"])
check("new user is not an admin", 4242 not in load("state.json").get("admins", []))

print("Run 11: a stranger with no invite is turned away over webhook")
sent.clear()
os.environ["TELEGRAM_UPDATE_JSON"] = json.dumps(
    {"update_id": 9003, "message": {"text": "hi", "chat": {"id": 5555}}}
)
run.main(["--no-check"])
check("stranger told it's invite-only", sent and "invite-only" in sent[0][1])
check("stranger not subscribed", 5555 not in load("state.json")["subscribers"])

print("Run 12: the new user (4242) tracks a different show than the admin")
sent.clear()
TV[9999] = {
    "name": "Foo Show", "status": "Returning Series", "number_of_seasons": 1,
    "seasons": [{"season_number": 1, "air_date": "2020-01-01"}],
    "last_episode_to_air": None, "next_episode_to_air": None,
}
os.environ["TELEGRAM_UPDATE_JSON"] = json.dumps(
    {"update_id": 9004, "message": {"text": "/add tv 9999", "chat": {"id": 4242}}}
)
run.main([])  # runs the check too, so 9999 gets its TMDB baseline right away
check("4242's own list has Foo Show", any(
    t["id"] == 9999 for t in load("titles.json")["users"]["4242"]["titles"]
))
check("777's list is unaffected", all(
    t["id"] != 9999 for t in load("titles.json")["users"]["777"]["titles"]
))

print("Run 13: Foo Show gets a new season - only 4242 hears about it, not 777")
sent.clear()
del os.environ["TELEGRAM_UPDATE_JSON"]  # webhook idle: no telegram commands this run
TV[9999] = dict(
    TV[9999], number_of_seasons=2,
    seasons=TV[9999]["seasons"] + [{"season_number": 2, "air_date": None}],
)
run.main([])
check("exactly one alert sent", len(sent) == 1)
check("alert went to 4242, not 777", sent[0][0] == 4242)
check("alert is about Foo Show", "Foo Show" in sent[0][1])
del os.environ["TELEGRAM_WEBHOOK_MODE"]

print(f"\n{'FAILED' if fails else 'PASSED'} ({fails} failing)")
sys.exit(1 if fails else 0)
