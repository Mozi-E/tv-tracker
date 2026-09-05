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

### GitHub App auth for the Worker - not a dated task

The Cloudflare Worker authenticates to GitHub as a **GitHub App**
([`webhook/README.md`](webhook/README.md) step 1), not a fine-grained PAT. It
signs a short-lived JWT with the App's private key and exchanges it for a
~1-hour installation token on every request. The private key **does not
expire**, so there is nothing here on a calendar - no `data/maintenance.json`
entry for it.

The only reason to touch it: the key is compromised. Then, incident-driven,
not scheduled: **GitHub -> Settings -> Developer settings -> GitHub Apps ->
(your app) -> Private keys -> Delete**, generate a new one, convert it to
PKCS#8 (`webhook/README.md` step 1), update `GH_APP_PRIVATE_KEY` in
Cloudflare.

> This project used to authenticate with a fine-grained PAT (`GH_TOKEN`),
> which expired and needed manual renewal - that's why an older version of
> this doc had a dated `cloudflare-pat` reminder. The GitHub App migration
> removed that task entirely.

### 1. Annual secret rotation  (`rotate-secrets`)

Optional hygiene, once a year. Two independent secrets, do both:

**a. Bot token**

1. Telegram -> `@BotFather` -> `/revoke` -> pick this bot -> copy the new token.
2. GitHub: <https://github.com/Mozi-E/tv-tracker/settings/secrets/actions> ->
   edit `TELEGRAM_BOT_TOKEN` -> paste the new token.
3. Re-point the webhook at the new token (the secret_token stays the same -
   only the bot token in the URL changes):
   ```bash
   SECRET=$(cat ~/tvt_secret.txt)
   BOT_TOKEN='<new token from BotFather>'
   curl "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" -d "url=https://tv-tracker-webhook.eliormozess.workers.dev" -d "secret_token=$SECRET" -d 'allowed_updates=["message"]'
   ```

**b. Webhook secret**

1. Generate a new one and overwrite the local copy:
   ```bash
   openssl rand -hex 32 | tr -d '\n' > ~/tvt_secret.txt
   cat ~/tvt_secret.txt; echo
   ```
2. Cloudflare -> the Worker -> **Settings -> Variables and Secrets** -> edit
   `TELEGRAM_SECRET_TOKEN` -> paste the new value -> **Save** (redeploys).
3. Point Telegram at the same new value (use the *current* bot token here):
   ```bash
   SECRET=$(cat ~/tvt_secret.txt)
   BOT_TOKEN='<current bot token>'
   curl "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" -d "url=https://tv-tracker-webhook.eliormozess.workers.dev" -d "secret_token=$SECRET" -d 'allowed_updates=["message"]'
   ```
4. Verify end to end, without touching Telegram, before trusting it:
   ```bash
   curl -i -X POST "https://tv-tracker-webhook.eliormozess.workers.dev" -H "X-Telegram-Bot-Api-Secret-Token: $SECRET" -H "Content-Type: application/json" --data '{"update_id":1,"message":{"text":"/list","chat":{"id":5179879702},"from":{"id":5179879702}}}'
   ```
   Expect `200 dispatched`. See [`webhook/README.md`](webhook/README.md#verifying-the-github-app-auth-directly)
   for what other status codes mean.

**c. Close out**

Bump this task's (`rotate-secrets`) `due` in `data/maintenance.json` +1 year
and commit.

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
