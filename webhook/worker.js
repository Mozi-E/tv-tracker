/**
 * Cloudflare Worker: Telegram webhook -> GitHub Actions.
 *
 * Telegram POSTs every message here. We verify it, then fire a
 * `repository_dispatch` event that starts the tv-tracker workflow with the
 * update payload attached, so a `/add` is processed within seconds instead of
 * waiting for the daily cron.
 *
 * Required environment variables (set as Worker secrets/vars):
 *   GH_TOKEN               fine-grained GitHub PAT, repo scoped, Contents: write
 *   GH_REPO                "Mozi-E/tv-tracker"
 *   TELEGRAM_SECRET_TOKEN  random string; also passed to Telegram setWebhook
 *   ALLOWED_USER_IDS       optional, comma-separated Telegram user ids allowed to use the bot
 */
export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("tv-tracker webhook up", { status: 200 });
    }

    if (
      !env.TELEGRAM_SECRET_TOKEN ||
      request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_SECRET_TOKEN
    ) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad json", { status: 400 });
    }

    const msg = update.message;
    if (!msg || typeof msg.text !== "string") {
      return new Response("ignored (no text message)", { status: 200 });
    }

    if (env.ALLOWED_USER_IDS) {
      const allowed = env.ALLOWED_USER_IDS.split(",").map((s) => s.trim());
      const fromId = String(msg.from && msg.from.id);
      if (!allowed.includes(fromId)) {
        return new Response("ignored (user not allowed)", { status: 200 });
      }
    }

    const resp = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tv-tracker-webhook",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        event_type: "telegram",
        client_payload: { update },
      }),
    });

    if (!resp.ok) {
      // Non-2xx so Telegram retries the delivery later.
      const detail = await resp.text();
      return new Response(`github dispatch failed: ${resp.status} ${detail}`, {
        status: 502,
      });
    }

    return new Response("dispatched", { status: 200 });
  },
};
