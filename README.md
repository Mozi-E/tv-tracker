# tv-tracker

Watches a list of TV shows and movies on [TMDB](https://developer.themoviedb.org/docs)
and sends a Telegram message when:

- a **new season** of a tracked show is announced or gets an air date, or
- a **sequel / related film** appears in a tracked movie's franchise (TMDB "collection").

You manage the watch-list entirely from Telegram (`/add`, `/list`, `/remove`).
A GitHub Actions workflow runs once a day: it reads your Telegram commands,
checks TMDB, messages you about anything new, and commits the updated state
back to this repo.

```
tvtracker/        the package
  config.py       reads secrets from the environment (never hard-coded)
  tmdb.py         tiny TMDB v3 client
  telegram.py     tiny Telegram Bot API client
  diff.py         pure logic: TMDB payload -> snapshot -> list of changes
  commands.py     the /add /list /remove chat interface
  run.py          orchestration (process commands, then run checks)
check.py          entry point  ->  python check.py
data/titles.json  your watch-list (managed over Telegram)
data/state.json   last-seen snapshot per title + Telegram bookkeeping
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
| `TELEGRAM_CHAT_ID`   | a chat id to always notify (optional)    | no       |

Secrets are only ever read from the environment (`tvtracker/config.py`); they
are never written to `data/` or committed.

> This repo is public, so `data/state.json` (which can contain the numeric
> chat ids of people who messaged the bot) is public too. If that matters to
> you, make the repo private and/or rely only on the `TELEGRAM_CHAT_ID` secret.

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
/help                                            command reference
```

Replies and alerts link each title straight to its TMDB page.

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

## Notes and limitations

- **Sequels** are detected via TMDB "collections". A standalone film that is
  not yet in a collection, or a sequel TMDB has not filed under the collection,
  will not trigger an alert until TMDB links it.
- The first time a title is seen, the current state is stored as a baseline and
  **no** alert is sent; you only hear about changes from then on.
- If a change is found but no Telegram target exists yet (nobody has run
  `/start` and `TELEGRAM_CHAT_ID` is unset), the alert is retried on the next
  run rather than lost.
- Commits made by the workflow use the built-in `GITHUB_TOKEN` and do not
  trigger further workflow runs.
