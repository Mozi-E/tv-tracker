"""Telegram chat interface for managing the watch-list.

`handle_update` is pure-ish: it mutates the in-memory titles_data / state dicts
and returns a list of replies for the caller to send. A reply is either
`(chat_id, text)` or `(chat_id, text, parse_mode)` - the "HTML" ones carry
clickable TMDB links. Only /add reaches out to TMDB (to resolve a name or a
URL to an id at add time).
"""
import html

from . import tmdb
from .store import key_for

HELP = (
    "TV & Movie Tracker\n"
    "\n"
    "/add &lt;name&gt;        - search TMDB and track a show or movie\n"
    "/add &lt;TMDB link&gt;    - track by pasting a themoviedb.org URL\n"
    "/add tv &lt;id&gt;       - track a show by its exact TMDB id\n"
    "/add movie &lt;id&gt;    - track a movie by its exact TMDB id\n"
    "/list              - show everything you track\n"
    "/remove &lt;number&gt;   - stop tracking item &lt;number&gt; from /list\n"
    "/help              - show this message\n"
    "\n"
    "Once a day I check TMDB. When a new season is announced or released, "
    "or a sequel shows up, I message you here."
)


def _link(media_type: str, tmdb_id, label: str) -> str:
    return f'<a href="{tmdb.web_url(media_type, tmdb_id)}">{html.escape(label)}</a>'


def handle_update(update: dict, titles_data: dict, state: dict):
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    if chat_id is None or not text:
        return []

    if chat_id not in state["subscribers"]:
        state["subscribers"].append(chat_id)

    if not text.startswith("/"):
        return [(chat_id, "Send /help to see what I can do.")]

    tokens = text.split()
    cmd = tokens[0].lower().split("@", 1)[0]  # strip @BotName in groups
    args = tokens[1:]

    if cmd in ("/start", "/help"):
        return [(chat_id, HELP, "HTML")]
    if cmd == "/list":
        return [(chat_id, _format_list(titles_data), "HTML")]
    if cmd == "/add":
        return _cmd_add(args, chat_id, titles_data)
    if cmd == "/remove":
        return _cmd_remove(args, chat_id, titles_data)
    return [(chat_id, f"Unknown command {cmd}. Try /help.")]


# ----------------------------------------------------------------------- add

def _cmd_add(args, chat_id, titles_data):
    if not args:
        return [(chat_id, "Usage: /add <name>   (or a TMDB link, or /add tv <id>)")]

    # TMDB URL anywhere in the argument(s)
    parsed = tmdb.parse_tmdb_url(" ".join(args))
    if parsed:
        return _add_by_id(parsed[0], parsed[1], chat_id, titles_data)

    # explicit form: /add tv 1399
    if len(args) >= 2 and args[0].lower() in ("tv", "movie") and args[1].isdigit():
        return _add_by_id(args[0].lower(), int(args[1]), chat_id, titles_data)

    # search form: /add The Matrix
    query = " ".join(args)
    try:
        results = tmdb.search(query, limit=5)
    except tmdb.TMDBError as e:
        return [(chat_id, f"Search failed: {e}")]
    if not results:
        return [(chat_id, f'Nothing found for "{query}".')]
    if len(results) == 1:
        r = results[0]
        return _do_add(r["media_type"], r["id"], r["title"], chat_id, titles_data)

    lines = [f'Several matches for "{html.escape(query)}" - reply with one of these:']
    for r in results:
        lines.append(
            f"- {_link(r['media_type'], r['id'], r['title'])} "
            f"({r['year'] or '--'}) [{r['media_type']}]   ->   /add {r['media_type']} {r['id']}"
        )
    return [(chat_id, "\n".join(lines), "HTML")]


def _add_by_id(mt, tmdb_id, chat_id, titles_data):
    try:
        details = tmdb.tv_details(tmdb_id) if mt == "tv" else tmdb.movie_details(tmdb_id)
    except tmdb.TMDBError as e:
        return [(chat_id, f"Could not fetch {mt} {tmdb_id}: {e}")]
    title = details.get("name") or details.get("title") or f"{mt} {tmdb_id}"
    return _do_add(mt, tmdb_id, title, chat_id, titles_data)


def _do_add(mt, tmdb_id, title, chat_id, titles_data):
    k = key_for(mt, tmdb_id)
    for t in titles_data["titles"]:
        if key_for(t["media_type"], t["id"]) == k:
            return [(chat_id, f'Already tracking {_link(mt, tmdb_id, t["title"])}.', "HTML")]
    titles_data["titles"].append(
        {"id": tmdb_id, "media_type": mt, "title": title, "added_by": chat_id}
    )
    what = "new seasons" if mt == "tv" else "sequels"
    kind = "show" if mt == "tv" else "movie"
    return [
        (
            chat_id,
            f"Now tracking the {kind} {_link(mt, tmdb_id, title)}. "
            f"I will alert you about {what}.",
            "HTML",
        )
    ]


# -------------------------------------------------------------------- remove

def _cmd_remove(args, chat_id, titles_data):
    lst = titles_data["titles"]
    if len(args) == 1 and args[0].isdigit():
        idx = int(args[0]) - 1
        if 0 <= idx < len(lst):
            removed = lst.pop(idx)
            return [
                (
                    chat_id,
                    f'Stopped tracking {_link(removed["media_type"], removed["id"], removed["title"])}.',
                    "HTML",
                )
            ]
        return [(chat_id, f"There is no item #{args[0]}. Check /list.")]
    if len(args) >= 2 and args[0].lower() in ("tv", "movie") and args[1].isdigit():
        k = key_for(args[0].lower(), int(args[1]))
        for i, t in enumerate(lst):
            if key_for(t["media_type"], t["id"]) == k:
                removed = lst.pop(i)
                return [
                    (
                        chat_id,
                        f'Stopped tracking {_link(removed["media_type"], removed["id"], removed["title"])}.',
                        "HTML",
                    )
                ]
        return [(chat_id, "That title is not in your list.")]
    return [(chat_id, "Usage: /remove <number from /list>")]


# ---------------------------------------------------------------------- list

def _format_list(titles_data):
    lst = titles_data["titles"]
    if not lst:
        return "You are not tracking anything yet. Use /add &lt;name&gt;."
    lines = ["You are tracking:"]
    for i, t in enumerate(lst, 1):
        tag = "TV " if t["media_type"] == "tv" else "MOV"
        lines.append(
            f"{i}. [{tag}] {_link(t['media_type'], t['id'], t['title'])}   (/remove {i})"
        )
    return "\n".join(lines)
