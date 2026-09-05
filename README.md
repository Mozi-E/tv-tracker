# tv-tracker

Watches a list of TV shows and movies on [TMDB](https://developer.themoviedb.org/docs)
and sends a Telegram message when:

- a **new season** of a tracked show is announced or gets an air date,
- a **new episode** of a tracked show airs (on/around its air date), or
- a **sequel / related film** appears in a tracked movie's franchise (TMDB "collection").

Each Telegram user who's been let in (see [Access control](#access-control))
gets **their own watch-list**, managed with `/add`, `/list`, `/remove`.
Two people tracking the same show only cost one TMDB check - the check is
shared, but each person only hears about the titles on their own list. A
GitHub Actions workflow runs once a day: it reads Telegram commands, checks
TMDB, messages the right people about anything new, and commits the updated
state back to this repo.

```
tvtracker/        the package
  config.py       reads secrets from the environment (never hard-coded)
  tmdb.py         tiny TMDB v3 client
  telegram.py     tiny Telegram Bot API client
  diff.py         pure logic: TMDB payload -> snapshot -> list of changes
  commands.py     the /add /list /remove /invite chat interface
  run.py          orchestration (process commands, then run checks)
check.py          entry point  ->  python check.py
data/titles.json  one watch-list per user (managed over Telegram)
data/state.json   last-seen snapshot per title (shared) + bot bookkeeping
data/invites.json share-link tokens created with /invite
.github/workflows/check.yml   the daily job
selftest.py       offline unit tests (no network, no deps)
itest.py          offline end-to-end test of a full daily cycle
```

## 1. Create a Telegram bot

1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot`, pick a name, then a username ending in `bot`.
3. BotFather replies with a **token** like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
   Keep it secret.
4. Open a chat with your new bot and send it `/start` (this is what lets the
   bot message you back).

Optional: to hard-wire a notification target, get your chat id by sending any
message to the bot and visiting
`https://api.telegram.org/bot<TOKEN>/getUpdates` — the number in
`message.chat.id` is your `TELEGRAM_CHAT_ID`.

## 2. Get a TMDB API key

1. Create an account at <https://www.themoviedb.org/>.
2. Go to **Settings -> API** (<https://www.themoviedb.org/settings/api>),
   request a developer key.
3. Copy the value labelled **API Key (v3 auth)**.

## 3. Add the GitHub secrets

In this repo: **Settings -> Secrets and variables -> Actions -> New repository secret**.
Add:

| Name                 | Value                                   | Required |
|----------------------|-----------------------------------------|----------|
| `TMDB_API_KEY`       | your TMDB v3 API key                     | yes      |
| `TELEGRAM_BOT_TOKEN` | the BotFather token                      | yes      |
| `TELEGRAM_CHAT_ID`   | a chat id CC'd on *every* alert (optional) | no     |

`TELEGRAM_CHAT_ID` is an always-CC address, not an access-control mechanism -
it gets a copy of every user's alerts and maintenance reminders regardless of
whose title it is. Leave it unset unless you specifically want one place that
sees everything. Secrets are only ever read from the environment
(`tvtracker/config.py`); they are never written to `data/` or committed.

> This repo is public, so `data/state.json` and `data/titles.json` (which can
> contain the numeric chat ids of everyone who's been let in, and what each of
> them tracks) are public too. If that matters to you, make the repo private.

## 4. Run it

The workflow `.github/workflows/check.yml` runs daily (`cron: "17 6 * * *"`,
UTC) and can also be started by hand from the **Actions** tab
(**Run workflow**). Because it only runs once a day, a title you add over
Telegram is picked up on the next run — hit **Run workflow** if you want it
sooner, or edit the cron to run more often.

### Optional: instant updates

For near-instant handling of your Telegram commands (instead of once a day),
set up the Cloudflare Worker webhook in [`webhook/README.md`](webhook/README.md).
Telegram then pushes every message to the Worker, which triggers this workflow
via `repository_dispatch` within seconds. The daily cron still runs the TMDB
check.

## 5. Use it from Telegram

```
/add The Bear                                    search TMDB, track the top match
/add Dune                                        if ambiguous, pick from a list
/add https://www.themoviedb.org/tv/1399          track by pasting a TMDB link
/add tv 1399                                     track by exact TMDB id
/add movie 603                                   track by exact TMDB id
/list                                            show everything you track
/remove 2                                        stop tracking item #2 from /list
/invite [uses] [days]                            (admin) share-link, default 1 use / 7 days
/help                                            command reference
```

Replies and alerts link each title straight to its TMDB page.

### Access control

The bot is invite-only. The very first person to message it becomes its
**admin** (no setup needed - this happens automatically on a fresh
deployment). An admin runs `/invite` to get a link like
`https://t.me/<bot_username>?start=<token>`; whoever opens it and taps
**Start** gets access immediately, no further action from you or them. By
default a link is good for 1 use and expires in 7 days -
`/invite 5 30` makes one good for 5 uses over 30 days.

Access is enforced by the bot itself (`tvtracker/commands.py`: `admins` /
`subscribers` / invite tokens in `data/state.json` and `data/invites.json`),
not by the Cloudflare Worker - the Worker forwards every message it receives
and lets the bot decide. Being let in only grants your own watch-list and your
own alerts - it doesn't expose anyone else's titles, and `/invite` is
admin-only so an ordinary user can't grant further access themselves.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then fill in TMDB_API_KEY and TELEGRAM_BOT_TOKEN

python selftest.py        # offline unit tests
python itest.py           # offline end-to-end test
python check.py           # a real run against TMDB + Telegram using your .env
```

`check.py` accepts `--no-telegram` (skip reading/sending Telegram) and
`--no-check` (skip the TMDB comparison) for debugging.

## Maintenance

Close to none - see [`MAINTENANCE.md`](MAINTENANCE.md). Dated upkeep tasks live
in [`data/maintenance.json`](data/maintenance.json) and the bot messages you
20 / 10 / 1 days before each one (and if it goes overdue).

## Notes and limitations

- **Sequels** are detected via TMDB "collections". A standalone film that is
  not yet in a collection, or a sequel TMDB has not filed under the collection,
  will not trigger an alert until TMDB links it.
- The first time a title is seen, the current state is stored as a baseline and
  **no** alert is sent; you only hear about changes from then on.
- **Episode** alerts fire for `next`/`last_episode_to_air` on or within
  `EPISODE_ALERT_WINDOW_DAYS` (3) of the air date, so a delayed or missed daily
  run still catches it; each episode is announced once. The episode that was
  already "next" when you added the show is treated as known and only alerts
  once it actually airs.
- If a change is found but no Telegram target exists yet (nobody has run
  `/start` and `TELEGRAM_CHAT_ID` is unset), the alert is retried on the next
  run rather than lost.
- Commits made by the workflow use the built-in `GITHUB_TOKEN` and do not
  trigger further workflow runs.
