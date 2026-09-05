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

## 1. GitHub App for the Worker (not a PAT)

The Worker authenticates to GitHub as a **GitHub App**, not a personal access
token. A PAT expires and needs manual renewal; a GitHub App's private key does
not expire, so this is a one-time setup instead of a recurring chore.

**Create the App:**

1. <https://github.com/settings/apps/new>
2. **GitHub App name:** anything unique, e.g. `elior-tv-tracker-dispatch`
3. **Homepage URL:** anything, e.g. `https://github.com/Mozi-E/tv-tracker`
4. **Webhook:** untick **Active** (this app only makes outbound calls; it
   doesn't need to receive GitHub webhooks)
5. **Permissions -> Repository permissions -> Contents:** **Read and write**
   (the only permission `repository_dispatch` needs)
6. **Where can this GitHub App be installed:** *Only on this account*
7. **Create GitHub App**
8. Note the **App ID** shown at the top of the app's settings page.
9. Scroll to **Private keys -> Generate a private key**. A `.pem` file
   downloads - this is `GH_APP_PRIVATE_KEY`'s source, keep it safe.
10. In the left sidebar, **Install App** -> choose your account -> **Only
    select repositories** -> `Mozi-E/tv-tracker` -> **Install**.
11. After installing, the URL bar shows
    `.../settings/installations/<NUMBER>` - that `<NUMBER>` is the
    **Installation ID**.

**Convert the downloaded key** (GitHub gives PKCS#1, the Worker's Web Crypto
API needs PKCS#8 - one-time, offline, no ongoing maintenance):

```bash
openssl pkcs8 -topk8 -inform PEM -outform PEM -in ~/Downloads/*.private-key.pem -out ~/tvt_app_key.pem -nocrypt
cat ~/tvt_app_key.pem
```

The output starts with `-----BEGIN PRIVATE KEY-----` (not `RSA PRIVATE KEY`).
Copy the whole thing, header/footer included - that's `GH_APP_PRIVATE_KEY`.

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
   | `GH_APP_ID` | Text | the App ID from step 1 |
   | `GH_APP_INSTALLATION_ID` | Text | the installation ID from step 1 |
   | `GH_APP_PRIVATE_KEY` | Secret | the PKCS#8 PEM from step 1 (multi-line, paste as-is) |
   | `TELEGRAM_SECRET_TOKEN` | Secret | your `WEBHOOK_SECRET` |
   | `ALLOWED_USER_IDS` | Secret | *(optional)* your numeric Telegram user id |

   If you're migrating from the old PAT setup: delete the `GH_TOKEN` variable,
   it's no longer used.
4. Note the URL: `https://tv-tracker-webhook.<your-subdomain>.workers.dev`.

### Option B - wrangler CLI

```bash
cd webhook
npx wrangler login
npx wrangler secret put GH_APP_PRIVATE_KEY      # paste the PKCS#8 PEM
npx wrangler secret put TELEGRAM_SECRET_TOKEN   # paste WEBHOOK_SECRET
npx wrangler secret put ALLOWED_USER_IDS        # optional
```

Edit `GH_APP_ID` and `GH_APP_INSTALLATION_ID` into `wrangler.toml` (or set them
as plain vars in the dashboard), then:

```bash
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

## Verifying the GitHub App auth directly

Same isolation trick as before - hit the Worker with a fake Telegram payload
and read the response, without touching Telegram at all:

```bash
SECRET=$(cat ~/tvt_secret.txt)
curl -i -X POST "https://tv-tracker-webhook.<your-subdomain>.workers.dev" \
  -H "X-Telegram-Bot-Api-Secret-Token: $SECRET" -H "Content-Type: application/json" \
  --data '{"update_id":1,"message":{"text":"/list","chat":{"id":1},"from":{"id":1}}}'
```

- `200 dispatched` -> the App auth and dispatch both worked; check the repo's
  **Actions** tab for a new `repository_dispatch` run.
- `502 github auth failed: 401 ...` -> `GH_APP_PRIVATE_KEY` doesn't match
  `GH_APP_ID`, or the key wasn't converted to PKCS#8.
- `502 github auth failed: 404 ...` (on the installation-token call) -> wrong
  `GH_APP_ID` or `GH_APP_INSTALLATION_ID`.
- `502 github dispatch failed: 404 ...` (after auth succeeded) -> the App is
  authenticated but isn't installed on `Mozi-E/tv-tracker`, or `GH_REPO` is
  wrong. Re-check step 1.10.
- `403 forbidden` -> the webhook secret mismatch issue, unrelated to the App -
  see the secret-rotation notes above.

## Notes

- Runs are serialised (`concurrency` in the workflow), so a fast burst of
  messages is handled one run at a time; each run's `getUpdates`-free handler
  processes just the update it was given. Duplicate deliveries (Telegram
  retries) are de-duped by `update_id` in `state.json`.
- Free Cloudflare Workers allow 100k requests/day - far more than a personal
  bot needs.
- The Worker mints a fresh JWT and installation token on every request rather
  than caching one; that's two extra HTTPS calls to GitHub per message, which
  is irrelevant at this volume.
- If the private key is ever compromised: **Settings -> Developer settings ->
  GitHub Apps -> (your app) -> Private keys -> Delete**, generate a new one,
  update `GH_APP_PRIVATE_KEY` in Cloudflare. No PAT-style expiry date to track.
