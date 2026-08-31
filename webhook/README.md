# Instant updates via webhook

Without this, the bot only reads your Telegram messages once a day (when the
cron workflow runs). With it, every message triggers the workflow within
seconds.

```
Telegram  --POST-->  Cloudflare Worker  --repository_dispatch-->  GitHub Actions
                                                                   runs check.py
```

The Worker holds no bot token and never talks back to Telegram - it only
forwards the update to GitHub. All replies are still sent by `check.py` from
inside Actions using `TELEGRAM_BOT_TOKEN`.

## 1. GitHub token for the Worker

Create a **fine-grained PAT** at
<https://github.com/settings/personal-access-tokens>:

- **Repository access** -> Only select repositories -> `Mozi-E/tv-tracker`
- **Permissions** -> Repository permissions -> **Contents: Read and write**
  (this is what "Create a repository dispatch event" needs)
- Copy the `github_pat_...` value.

## 2. A shared secret for the webhook

Pick any random string, e.g.

```bash
openssl rand -hex 32
```

Call it `WEBHOOK_SECRET` below. Telegram will send it back in the
`X-Telegram-Bot-Api-Secret-Token` header on every call; the Worker rejects
anything else.

## 3. Deploy the Worker

### Option A - dashboard (no install)

1. <https://dash.cloudflare.com/> -> **Workers & Pages** -> **Create** ->
   **Create Worker**. Name it `tv-tracker-webhook`, deploy the placeholder.
2. **Edit code** -> paste the contents of [`worker.js`](worker.js) -> **Deploy**.
3. **Settings -> Variables and Secrets**, add:
   | Name | Type | Value |
   |------|------|-------|
   | `GH_REPO` | Text | `Mozi-E/tv-tracker` |
   | `GH_TOKEN` | Secret | the `github_pat_...` from step 1 |
   | `TELEGRAM_SECRET_TOKEN` | Secret | your `WEBHOOK_SECRET` |
   | `ALLOWED_USER_IDS` | Secret | *(optional)* your numeric Telegram user id |
4. Note the URL: `https://tv-tracker-webhook.<your-subdomain>.workers.dev`.

### Option B - wrangler CLI

```bash
cd webhook
npx wrangler login
npx wrangler secret put GH_TOKEN                # paste the PAT
npx wrangler secret put TELEGRAM_SECRET_TOKEN   # paste WEBHOOK_SECRET
npx wrangler secret put ALLOWED_USER_IDS        # optional
npx wrangler deploy
```

`GH_REPO` comes from `wrangler.toml`. `wrangler deploy` prints the URL.

## 4. Point Telegram at the Worker

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -d "url=https://tv-tracker-webhook.<your-subdomain>.workers.dev" \
  -d "secret_token=<WEBHOOK_SECRET>" \
  -d "allowed_updates=[\"message\"]"
```

Check it:

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"
```

`"url"` should be your Worker and `"pending_update_count"` should fall to 0.

## 5. Tell the workflow a webhook is active

In the repo: **Settings -> Secrets and variables -> Actions -> Variables tab
-> New repository variable**

| Name | Value |
|------|-------|
| `TELEGRAM_WEBHOOK_MODE` | `1` |

This stops the daily scheduled run from calling `getUpdates` (Telegram returns
409 Conflict while a webhook is registered). Command handling now comes only
from webhook pushes; the scheduled run still does the daily TMDB check.

## Test it

Send `/list` to the bot. Within ~1 minute you should see a run appear under
the repo's **Actions** tab (event: `repository_dispatch`) and a reply in
Telegram.

## Going back to polling

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/deleteWebhook"
```

then delete the `TELEGRAM_WEBHOOK_MODE` variable. The daily cron resumes
reading messages with `getUpdates`.

## Notes

- Runs are serialised (`concurrency` in the workflow), so a fast burst of
  messages is handled one run at a time; each run's `getUpdates`-free handler
  processes just the update it was given. Duplicate deliveries (Telegram
  retries) are de-duped by `update_id` in `state.json`.
- Free Cloudflare Workers allow 100k requests/day - far more than a personal
  bot needs.
