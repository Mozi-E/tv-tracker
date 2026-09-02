# Maintenance

This project is close to zero-maintenance: everything runs on free tiers
(GitHub Actions on a public repo, Cloudflare Workers, TMDB, Telegram) and the
watch-list is managed from Telegram. Below is everything that ever needs a
human.

## Automated reminders

Dated tasks live in [`data/maintenance.json`](data/maintenance.json). On every
run, `tvtracker/maintenance.py` checks each task and sends a Telegram message
**20, 10, and 1 day before** the `due` date, plus an **overdue** notice if the
date passes. Each milestone is sent once; editing a task's `due` date re-arms
all of its reminders.

Reminders go to the same recipients as change alerts (everyone who has messaged
the bot, plus `TELEGRAM_CHAT_ID` if that secret is set). Set the
`TELEGRAM_CHAT_ID` repo secret to your own chat id if you want reminders to
reach only you.

### Editing tasks

Edit `data/maintenance.json` and commit. A task is:

```json
{
  "id": "short-slug",
  "title": "One line shown in the reminder",
  "due": "2026-12-01",
  "notes": "What to actually do when this comes up.",
  "notified": [],
  "due_seen": null
}
```

Leave `notified` / `due_seen` alone - the script manages them. To "snooze" or
mark a task done, change `due` to the next date; reminders re-arm automatically.

To test locally without waiting:

```bash
python -c "from datetime import date; from tvtracker import store, maintenance; \
d=store.load_maintenance(); print(maintenance.scan(d['tasks'], date(2026,11,25)))"
```

## The tasks

### 1. Renew the GitHub PAT in the Cloudflare Worker  (`cloudflare-pat`)

**The only thing scheduled to break.** The Worker calls the GitHub API with a
fine-grained Personal Access Token (`GH_TOKEN`). If you gave it an expiry, on
that day:

- `/add`, `/list`, `/remove` stop being instant - the Worker gets HTTP 401 from
  GitHub and Telegram messages are no longer forwarded.
- The **daily TMDB check still works** (it doesn't use the Worker).

Fix (~3 min):

1. <https://github.com/settings/personal-access-tokens> -> **Generate new token**
   -> fine-grained, repo `Mozi-E/tv-tracker`, **Contents: Read and write**.
2. Cloudflare -> the Worker -> **Settings -> Variables and Secrets** -> edit
   `GH_TOKEN` -> paste -> **Save** (redeploys).
3. In `data/maintenance.json` set `cloudflare-pat`'s `due` to the new expiry
   date (or `+90 days`) and commit.

> **Set the real date now:** open your token at the link above, read its
> expiry, and put it in `data/maintenance.json`. The seeded date is a guess.
> If you chose "No expiration", set `due` far in the future - nothing to do.

### 2. Annual secret rotation  (`rotate-secrets`)

Optional hygiene, once a year:

- `@BotFather` -> `/revoke` -> update `TELEGRAM_BOT_TOKEN` in
  <https://github.com/Mozi-E/tv-tracker/settings/secrets/actions>.
- New shared secret: `openssl rand -hex 32` -> update `TELEGRAM_SECRET_TOKEN`
  in Cloudflare **and** re-run `setWebhook` with the new value
  (see [`webhook/README.md`](webhook/README.md) step 4).
- Bump this task's `due` +1 year.

## Not scheduled - handle when prompted

- **GitHub Actions version warnings.** Every year or so a run shows a warning
  that `actions/checkout` / `actions/setup-python` target an old Node. Bump the
  `@vN` in [`.github/workflows/check.yml`](.github/workflows/check.yml) and push.
- **Dependency updates.** `requests` / `python-dotenv` are pinned in
  `requirements.txt`. Enable Dependabot (repo **Settings -> Security**) if you
  want PRs for these automatically.
- **Scheduled workflow auto-disabled.** GitHub disables a `schedule` workflow
  after 60 days with no repo commits. The daily run usually commits state, so
  this shouldn't happen; if it does, GitHub emails you and the Actions tab
  shows an **Enable workflow** button.

## Never

- TMDB API key and the Telegram bot token do not expire on their own.
- Cloudflare Workers runtime is managed; the free tier (100k req/day) is far
  above what a personal bot uses.
- `state.json` is size-bounded (`recent_update_ids` <= 100,
  `notified_episodes` <= 20 per title).
- No billing anywhere to watch.

## Monitoring (passive)

- Make sure <https://github.com/settings/notifications> emails you on **failed**
  workflow runs.
- If Telegram replies stop, check
  `https://api.telegram.org/bot<token>/getWebhookInfo` for `last_error_message`.
- The repo (code **and** state) is its own backup. Don't delete it.
