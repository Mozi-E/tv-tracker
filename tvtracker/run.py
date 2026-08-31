"""Orchestration: process Telegram commands, then check TMDB for updates.

Run once per day by .github/workflows/check.yml. Both steps mutate the
in-repo JSON files, which the workflow commits back afterwards.
"""
import sys

from . import config, telegram, tmdb
from .commands import handle_update
from .diff import diff_movie, diff_tv, movie_snapshot, tv_snapshot
from .store import (
    key_for,
    load_state,
    load_titles,
    save_state,
    save_titles,
)


def process_commands(titles_data: dict, state: dict) -> None:
    offset = state.get("telegram_offset", 0)
    try:
        updates = telegram.get_updates(offset=offset)
    except telegram.TelegramError as e:
        print(f"[telegram] getUpdates failed: {e}")
        return

    for up in updates:
        state["telegram_offset"] = up["update_id"] + 1
        try:
            replies = handle_update(up, titles_data, state)
        except Exception as e:  # one bad message must not abort the run
            print(f"[commands] error on update {up.get('update_id')}: {e}")
            replies = []
        for chat_id, text in replies:
            try:
                telegram.send_message(chat_id, text)
            except telegram.TelegramError as e:
                print(f"[telegram] reply to {chat_id} failed: {e}")
    print(f"[telegram] processed {len(updates)} update(s)")


def _notify(state: dict, text: str) -> bool:
    targets = set(state.get("subscribers", []))
    cid = config.telegram_chat_id()
    if cid:
        targets.add(int(cid) if cid.lstrip("-").isdigit() else cid)
    delivered = False
    for chat_id in targets:
        try:
            telegram.send_message(chat_id, text)
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
            body = f'Update for "{name}":\n' + "\n".join(f"- {c}" for c in changes)
            print(f"[change] {k}: {changes}")
            if not _notify(state, body):
                # keep old snapshot so we retry the alert once a target exists
                print(f"[change] {k}: not delivered, will retry next run")
                continue
        st_titles[k] = snap

    live = {key_for(t["media_type"], t["id"]) for t in titles_data["titles"]}
    for stale in [k for k in st_titles if k not in live]:
        del st_titles[stale]


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    titles_data = load_titles()
    state = load_state()

    if "--no-telegram" not in argv:
        process_commands(titles_data, state)
    if "--no-check" not in argv:
        run_checks(titles_data, state)

    save_titles(titles_data)
    save_state(state)
    print("done.")


if __name__ == "__main__":
    main()
