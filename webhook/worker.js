/**
 * Cloudflare Worker: Telegram webhook -> GitHub Actions.
 *
 * Telegram POSTs every message here. We verify it, then fire a
 * `repository_dispatch` event that starts the tv-tracker workflow with the
 * update payload attached, so a `/add` is processed within seconds instead of
 * waiting for the daily cron.
 *
 * Auth to GitHub is via a GitHub App installation token, not a PAT: we sign a
 * short-lived JWT with the App's private key and exchange it for a ~1-hour
 * installation token on every request. The private key does not expire, so
 * there is nothing here to renew on a schedule (unlike a fine-grained PAT).
 *
 * Required environment variables:
 *   GH_APP_ID                the GitHub App's numeric App ID
 *   GH_APP_INSTALLATION_ID   the installation ID for this repo
 *   GH_APP_PRIVATE_KEY       the App's private key, PKCS#8 PEM (see webhook/README.md)
 *   GH_REPO                  "Mozi-E/tv-tracker"
 *   TELEGRAM_SECRET_TOKEN    random string; also passed to Telegram setWebhook
 *   ALLOWED_USER_IDS         optional, comma-separated Telegram user ids allowed to use the bot
 */

function base64url(input) {
  const bytes = typeof input === "string" ? new TextEncoder().encode(input) : new Uint8Array(input);
  let str = "";
  for (const b of bytes) str += String.fromCharCode(b);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function importPrivateKey(pem) {
  const contents = pem
    .replace(/-----BEGIN PRIVATE KEY-----/, "")
    .replace(/-----END PRIVATE KEY-----/, "")
    .replace(/\s+/g, "");
  const der = Uint8Array.from(atob(contents), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey(
    "pkcs8",
    der.buffer,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["sign"],
  );
}

async function createAppJWT(appId, privateKeyPem) {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: "RS256", typ: "JWT" };
  const payload = { iat: now - 60, exp: now + 600, iss: Number(appId) };
  const unsigned = `${base64url(JSON.stringify(header))}.${base64url(JSON.stringify(payload))}`;
  const key = await importPrivateKey(privateKeyPem);
  const sig = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5",
    key,
    new TextEncoder().encode(unsigned),
  );
  return `${unsigned}.${base64url(sig)}`;
}

async function getInstallationToken(env) {
  const jwt = await createAppJWT(env.GH_APP_ID, env.GH_APP_PRIVATE_KEY);
  const resp = await fetch(
    `https://api.github.com/app/installations/${env.GH_APP_INSTALLATION_ID}/access_tokens`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${jwt}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tv-tracker-webhook",
      },
    },
  );
  if (!resp.ok) {
    throw new Error(`installation token failed: ${resp.status} ${await resp.text()}`);
  }
  return (await resp.json()).token;
}

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

    let token;
    try {
      token = await getInstallationToken(env);
    } catch (e) {
      return new Response(`github auth failed: ${e.message}`, { status: 502 });
    }

    const resp = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
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
