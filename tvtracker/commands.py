"""Telegram chat interface for managing the watch-list.

`handle_update` is pure-ish: it mutates the in-memory titles_data / state dicts
and returns a list of replies for the caller to send. A reply is either
`(chat_id, text)` or `(chat_id, text, parse_mode)` - the "HTML" ones carry
clickable TMDB links. Only /add reaches out to TMDB (to resolve a name or a
URL to an id at add time).
"""
import html
import secrets as _secrets
from datetime import date, timedelta

from . import tmdb, telegram
from .diff import movie_snapshot, tv_snapshot
from .store import key_for, user_titles

HELP = (
    "TV & Movie Tracker\n"
    "\n"
    "/add &lt;name&gt;        - search TMDB and track a show or movie\n"
    "/add &lt;TMDB link&gt;    - track by pasting a themoviedb.org URL\n"
    "/add tv &lt;id&gt;       - track a show by its exact TMDB id\n"
    "/add movie &lt;id&gt;    - track a movie by its exact TMDB id\n"
    "/list              - show everything you track\n"
    "/remove &lt;number&gt;   - stop tracking item &lt;number&gt; from /list\n"
    "/where &lt;number&gt;    - where to watch it in Israel\n"
    "/where &lt;name&gt;      - same, by name/link/id instead of list number\n"
    "/help              - show this message\n"
    "/invite [uses] [days] - (admin only) create a share link, default 1 use / 7 days\n"
    "\n"
    "Once a day I check TMDB. When a new season is announced or released, "
    "or a sequel shows up, I message you here."
)

NOT_INVITED = (
    "This bot is invite-only. Ask whoever told you about it to send you an "
    "invite link (/invite)."
)

DEFAULT_INVITE_USES = 1
DEFAULT_INVITE_DAYS = 7


def _link(media_type: str, tmdb_id, label: str) -> str:
    return f'<a href="{tmdb.web_url(media_type, tmdb_id)}">{html.escape(label)}</a>'


def handle_update(update: dict, titles_data: dict, state: dict, invites: dict):
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    if chat_id is None or not text:
        return []

    tokens = text.split()
    cmd = tokens[0].lower().split("@", 1)[0] if tokens else ""  # strip @BotName in groups
    args = tokens[1:]

    if chat_id not in state["subscribers"]:
        # bootstrap: on a brand-new deployment, the first person to talk to
        # the bot becomes its admin - no manual state.json edit required.
        if not state["subscribers"] and not state.get("admins"):
            state["subscribers"].append(chat_id)
            state.setdefault("admins", []).append(chat_id)
            return [(chat_id, "You're the first user, so I made you the admin.\n\n" + HELP, "HTML")]
        if cmd == "/start" and args:
            return _redeem_invite(args[0], chat_id, state, invites)
        return [(chat_id, NOT_INVITED)]

    if not text.startswith("/"):
        return [(chat_id, "Send /help to see what I can do.")]

    if cmd in ("/start", "/help"):
        return [(chat_id, HELP, "HTML")]
    if cmd == "/list":
        return [(chat_id, _format_list(titles_data, chat_id), "HTML")]
    if cmd == "/add":
        return _cmd_add(args, chat_id, titles_data)
    if cmd == "/remove":
        return _cmd_remove(args, chat_id, titles_data)
    if cmd == "/where":
        return _cmd_where(args, chat_id, titles_data)
    if cmd == "/invite":
        return _cmd_invite(args, chat_id, state, invites)
    return [(chat_id, f"Unknown command {cmd}. Try /help.")]


# -------------------------------------------------------------------- invite

def _redeem_invite(token, chat_id, state, invites):
    inv = invites.get("invites", {}).get(token)
    if not inv:
        return [(chat_id, "That invite link is invalid.")]
    if inv.get("expires") and date.today().isoformat() > inv["expires"]:
        return [(chat_id, "That invite link has expired. Ask for a new one.")]
    if inv.get("uses", 0) >= inv.get("max_uses", DEFAULT_INVITE_USES):
        return [(chat_id, "That invite link has already been used up.")]

    inv["uses"] = inv.get("uses", 0) + 1
    inv.setdefault("used_by", []).append(chat_id)
    state["subscribers"].append(chat_id)
    return [(chat_id, "You're in!\n\n" + HELP, "HTML")]


def _cmd_invite(args, chat_id, state, invites):
    if chat_id not in state.get("admins", []):
        return [(chat_id, "Only an admin can create invite links. Ask them for one.")]

    max_uses = int(args[0]) if len(args) >= 1 and args[0].isdigit() else DEFAULT_INVITE_USES
    days = int(args[1]) if len(args) >= 2 and args[1].isdigit() else DEFAULT_INVITE_DAYS
    token = _secrets.token_urlsafe(6)
    expires = (date.today() + timedelta(days=days)).isoformat()
    invites.setdefault("invites", {})[token] = {
        "created_by": chat_id,
        "max_uses": max_uses,
        "uses": 0,
        "expires": expires,
    }

    username = state.get("bot_username")
    if not username:
        try:
            username = telegram.get_me().get("username")
            if username:
                state["bot_username"] = username
        except telegram.TelegramError:
            username = None

    if not username:
        return [
            (
                chat_id,
                f"Invite token (couldn't look up the bot's @username - build the "
                f"link yourself): {token}\nValid for {max_uses} use(s), expires {expires}.",
            )
        ]
    link = f"https://t.me/{username}?start={token}"
    return [
        (
            chat_id,
            f"Invite link, {max_uses} use(s), expires {expires}:\n{link}\n\n"
            "Whoever opens it and taps Start gets access - no other setup needed.",
        )
    ]


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
    return _do_add(mt, tmdb_id, title, chat_id, titles_data, details=details)


def _pending_tv_note(snap: dict):
    """A season that's already announced/dated but hasn't aired yet, if any."""
    today = date.today().isoformat()
    pending = [
        (int(num), air_date)
        for num, air_date in snap.get("seasons", {}).items()
        if not air_date or air_date > today
    ]
    if not pending:
        return None
    num, air_date = max(pending)  # the furthest-along season not yet out
    if air_date:
        return f"Note: Season {num} is already announced - premieres {air_date}."
    return f"Note: Season {num} is already announced (no air date yet)."


def _pending_movie_note(snap: dict, tmdb_id):
    """A franchise entry that's already announced/dated but not released yet."""
    today = date.today().isoformat()
    pending = []
    for pid, info in snap.get("parts", {}).items():
        if int(pid) == tmdb_id:
            continue
        rd = info.get("release_date")
        if not rd or rd > today:
            pending.append((rd, info.get("title") or "Untitled"))
    if not pending:
        return None
    dated = sorted((p for p in pending if p[0]), key=lambda p: p[0])
    rd, title = dated[0] if dated else pending[0]
    if rd:
        return f'Note: "{html.escape(title)}" is already announced - releases {rd}.'
    return f'Note: "{html.escape(title)}" is already announced (no release date yet).'


def _do_add(mt, tmdb_id, title, chat_id, titles_data, details=None):
    lst = user_titles(titles_data, chat_id)
    k = key_for(mt, tmdb_id)
    for t in lst:
        if key_for(t["media_type"], t["id"]) == k:
            return [(chat_id, f'Already tracking {_link(mt, tmdb_id, t["title"])}.', "HTML")]
    lst.append({"id": tmdb_id, "media_type": mt, "title": title})
    what = "new seasons" if mt == "tv" else "sequels"
    kind = "show" if mt == "tv" else "movie"
    msg = (
        f"Now tracking the {kind} {_link(mt, tmdb_id, title)}. "
        f"I will alert you about {what}."
    )

    # Best-effort "already announced" note. This is a nice-to-have on top of
    # a successful add, so any failure here (network, unexpected shape) must
    # never break the confirmation itself.
    if details is None:
        try:
            details = tmdb.tv_details(tmdb_id) if mt == "tv" else tmdb.movie_details(tmdb_id)
        except Exception:
            details = None

    note = None
    if details:
        try:
            if mt == "tv":
                note = _pending_tv_note(tv_snapshot(details))
            else:
                coll = details.get("belongs_to_collection")
                if coll:
                    cd = tmdb.collection_details(coll["id"])
                    note = _pending_movie_note(movie_snapshot(details, cd), tmdb_id)
        except Exception:
            note = None
    if note:
        msg += "\n" + note

    return [(chat_id, msg, "HTML")]


# -------------------------------------------------------------------- remove

def _cmd_remove(args, chat_id, titles_data):
    lst = user_titles(titles_data, chat_id)
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


# ----------------------------------------------------------------- where

_PROVIDER_KINDS = [
    ("flatrate", "Subscription"),
    ("free", "Free"),
    ("ads", "Free with ads"),
    ("rent", "Rent"),
    ("buy", "Buy"),
]


def _format_providers(mt, tmdb_id, title, data):
    il = (data.get("results") or {}).get("IL") or {}
    lines = []
    for key, label in _PROVIDER_KINDS:
        providers = il.get(key)
        if providers:
            names = ", ".join(html.escape(p["provider_name"]) for p in providers)
            lines.append(f"- {label}: {names}")

    header = f"Where to watch {_link(mt, tmdb_id, title)} in Israel:"
    if not lines:
        return (
            f"No Israeli streaming info on TMDB for {_link(mt, tmdb_id, title)} yet "
            "(JustWatch coverage varies by title)."
        )
    body = header + "\n" + "\n".join(lines)
    if il.get("link"):
        body += f'\n<a href="{il["link"]}">Full details on TMDB</a>'
    return body


def _cmd_where(args, chat_id, titles_data):
    if not args:
        return [(chat_id, "Usage: /where <number from /list>   (or a name, a TMDB link, or /where tv <id>)")]

    if len(args) == 1 and args[0].isdigit():
        lst = user_titles(titles_data, chat_id)
        idx = int(args[0]) - 1
        if 0 <= idx < len(lst):
            t = lst[idx]
            return _where_by_id(t["media_type"], t["id"], t["title"], chat_id)
        return [(chat_id, f"There is no item #{args[0]}. Check /list.")]

    parsed = tmdb.parse_tmdb_url(" ".join(args))
    if parsed:
        return _where_by_id(parsed[0], parsed[1], None, chat_id)

    if len(args) >= 2 and args[0].lower() in ("tv", "movie") and args[1].isdigit():
        return _where_by_id(args[0].lower(), int(args[1]), None, chat_id)

    query = " ".join(args)
    try:
        results = tmdb.search(query, limit=5)
    except tmdb.TMDBError as e:
        return [(chat_id, f"Search failed: {e}")]
    if not results:
        return [(chat_id, f'Nothing found for "{query}".')]
    if len(results) == 1:
        r = results[0]
        return _where_by_id(r["media_type"], r["id"], r["title"], chat_id)

    lines = [f'Several matches for "{html.escape(query)}" - reply with one of these:']
    for r in results:
        lines.append(
            f"- {_link(r['media_type'], r['id'], r['title'])} "
            f"({r['year'] or '--'}) [{r['media_type']}]   ->   /where {r['media_type']} {r['id']}"
        )
    return [(chat_id, "\n".join(lines), "HTML")]


def _where_by_id(mt, tmdb_id, title, chat_id):
    if title is None:
        try:
            details = tmdb.tv_details(tmdb_id) if mt == "tv" else tmdb.movie_details(tmdb_id)
        except tmdb.TMDBError as e:
            return [(chat_id, f"Could not fetch {mt} {tmdb_id}: {e}")]
        title = details.get("name") or details.get("title") or f"{mt} {tmdb_id}"
    try:
        data = tmdb.watch_providers(mt, tmdb_id)
    except tmdb.TMDBError as e:
        return [(chat_id, f"Could not fetch streaming info: {e}")]
    return [(chat_id, _format_providers(mt, tmdb_id, title, data), "HTML")]


# ---------------------------------------------------------------------- list

def _format_list(titles_data, chat_id):
    lst = user_titles(titles_data, chat_id)
    if not lst:
        return "You are not tracking anything yet. Use /add &lt;name&gt;."
    lines = ["You are tracking:"]
    for i, t in enumerate(lst, 1):
        tag = "TV " if t["media_type"] == "tv" else "MOV"
        lines.append(
            f"{i}. [{tag}] {_link(t['media_type'], t['id'], t['title'])}   (/remove {i})"
        )
    return "\n".join(lines)
