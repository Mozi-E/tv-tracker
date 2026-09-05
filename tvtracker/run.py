"""Orchestration: process Telegram commands, then check TMDB for updates.

Run once per day by .github/workflows/check.yml. Both steps mutate the
in-repo JSON files, which the workflow commits back afterwards.
"""
import html
import json
import os
import sys

from . import config, maintenance, telegram, tmdb
from .commands import handle_update
from .diff import diff_movie, diff_tv, movie_snapshot, tv_snapshot
from .store import (
    key_for,
    load_invites,
    load_maintenance,
    load_state,
    load_titles,
    save_invites,
    save_maintenance,
    save_state,
    save_titles,
)


def process_commands(titles_data: dict, state: dict, invites: dict) -> None:
    """Handle Telegram messages.

    Three modes:
    - webhook push: a single update JSON is handed to us in TELEGRAM_UPDATE_JSON
      (the Cloudflare Worker forwards it via repository_dispatch);
    - webhook idle: TELEGRAM_WEBHOOK_MODE is set but no update this run -> nothing
      to do (getUpdates would 409 while a webhook is registered);
    - polling: no webhook -> long-poll-free getUpdates, tracked by an offset.
    """
    raw = os.environ.get("TELEGRAM_UPDATE_JSON", "").strip()
    if raw and raw not in ("null", "None"):
        try:
            payload = json.loads(raw)
        except ValueError as e:
            print(f"[telegram] bad TELEGRAM_UPDATE_JSON: {e}")
            return
        updates = payload if isinstance(payload, list) else [payload]
        _handle_updates(updates, titles_data, state, invites, webhook=True)
        return

    if os.environ.get("TELEGRAM_WEBHOOK_MODE", "").strip():
        print("[telegram] webhook mode, no update this run - skipping getUpdates")
        return

    try:
        updates = telegram.get_updates(offset=state.get("telegram_offset", 0))
    except telegram.TelegramError as e:
        print(f"[telegram] getUpdates failed: {e}")
        return
    _handle_updates(updates, titles_data, state, invites, webhook=False)


def _handle_updates(updates, titles_data: dict, state: dict, invites: dict, webhook: bool) -> None:
    seen = state.setdefault("recent_update_ids", [])
    handled = 0
    for up in updates:
        if not isinstance(up, dict):
            continue
        uid = up.get("update_id")
        if webhook and uid is not None and uid in seen:
            print(f"[telegram] duplicate update {uid}, skipping")
            continue
        if not webhook and uid is not None:
            state["telegram_offset"] = uid + 1
        try:
            replies = handle_update(up, titles_data, state, invites)
        except Exception as e:  # one bad message must not abort the run
            print(f"[commands] error on update {uid}: {e}")
            replies = []
        for reply in replies:
            chat_id, text = reply[0], reply[1]
            parse_mode = reply[2] if len(reply) > 2 else None
            try:
                telegram.send_message(chat_id, text, parse_mode=parse_mode)
            except telegram.TelegramError as e:
                print(f"[telegram] reply to {chat_id} failed: {e}")
        if uid is not None:
            seen.append(uid)
        handled += 1
    del seen[:-100]  # keep only the 100 most recent ids
    print(f"[telegram] processed {handled} update(s) ({'webhook' if webhook else 'poll'})")


def _notify(state: dict, text: str, parse_mode: str = None) -> bool:
    targets = set(state.get("subscribers", []))
    cid = config.telegram_chat_id()
    if cid:
        targets.add(int(cid) if cid.lstrip("-").isdigit() else cid)
    delivered = False
    for chat_id in targets:
        try:
            telegram.send_message(chat_id, text, parse_mode=parse_mode)
            delivered = True
        except telegram.TelegramError as e:
            print(f"[telegram] notify {chat_id} failed: {e}")
    if not targets:
        print("[telegram] no notification target yet (send /start to the bot)")
    return delivered


def run_checks(titles_data: dict, state: dict) -> None:
    st_titles = state.setdefault("titles", {})

    for t in titles_data["titles"]:
        mt, tmdb_id, name = t["media_type"], t["id"], t["title"]
        k = key_for(mt, tmdb_id)
        try:
            if mt == "tv":
                snap = tv_snapshot(tmdb.tv_details(tmdb_id))
                changes = diff_tv(st_titles.get(k), snap)
            else:
                md = tmdb.movie_details(tmdb_id)
                coll = md.get("belongs_to_collection")
                cd = tmdb.collection_details(coll["id"]) if coll else None
                snap = movie_snapshot(md, cd)
                changes = diff_movie(st_titles.get(k), snap)
        except tmdb.TMDBError as e:
            print(f"[tmdb] {k} ({name}): {e}")
            continue

        if changes:
            link = tmdb.web_url(mt, tmdb_id)
            body = (
                f'Update for <a href="{link}">{html.escape(name)}</a>:\n'
                + "\n".join(f"- {html.escape(c)}" for c in changes)
            )
            print(f"[change] {k}: {changes}")
            if not _notify(state, body, parse_mode="HTML"):
                # keep old snapshot so we retry the alert once a target exists
                print(f"[change] {k}: not delivered, will retry next run")
                continue
        st_titles[k] = snap

    live = {key_for(t["media_type"], t["id"]) for t in titles_data["titles"]}
    for stale in [k for k in st_titles if k not in live]:
        del st_titles[stale]


def run_maintenance(state: dict) -> None:
    data = load_maintenance()
    pending = maintenance.scan(data["tasks"])
    for task, milestone, text in pending:
        print(f"[maintenance] due: {task.get('id', task.get('title'))} ({milestone})")
        if _notify(state, "\U0001f527 " + text):
            maintenance.mark(task, milestone)
        else:
            print("[maintenance] reminder not delivered, will retry next run")
    if not pending:
        print(f"[maintenance] {len(data['tasks'])} task(s), nothing due")
    save_maintenance(data)


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    titles_data = load_titles()
    state = load_state()
    invites = load_invites()

    if "--no-telegram" not in argv:
        process_commands(titles_data, state, invites)
    if "--no-check" not in argv:
        run_checks(titles_data, state)
    if "--no-maintenance" not in argv:
        run_maintenance(state)

    save_titles(titles_data)
    save_state(state)
    save_invites(invites)
    print("done.")


if __name__ == "__main__":
    main()
