/**
 * Soft4P Telegram Bot — On-Demand Wake-on-Message Cloudflare Worker
 *
 * Required Cloudflare Bindings & Environment Variables (Secrets):
 * - DB (D1 Database Binding) : Any free Cloudflare D1 database
 * - BOT_TOKEN                : Telegram Bot Token
 * - WEBHOOK_SECRET           : Random secret string for Telegram webhook verification (optional, defaults to WORKER_SECRET)
 * - WORKER_SECRET            : Random secret string shared with bot.py (/pop-updates)
 * - GH_TOKEN                 : GitHub Personal Access Token (repo + workflow scope)
 * - GH_OWNER                 : GitHub username (e.g. mrbetadxyz-cmd)
 * - GH_REPO                  : GitHub repository name (e.g. Tikadown)
 * - GH_WORKFLOW              : Workflow filename (default: bot.yml)
 * - GH_BRANCH                : Branch name (default: main)
 * - SELF_WEBHOOK_URL         : Full webhook URL for Cron safety-net (e.g. https://.../webhook)
 */

const DISPATCH_COOLDOWN_SEC = 25;

async function ensureSchema(db) {
  await db
    .prepare(
      `CREATE TABLE IF NOT EXISTS pending_updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payload TEXT NOT NULL,
        chat_id INTEGER,
        status_msg_id INTEGER,
        created_at INTEGER NOT NULL
      )`
    )
    .run();

  await db
    .prepare(
      `CREATE TABLE IF NOT EXISTS worker_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_dispatch_at INTEGER NOT NULL DEFAULT 0
      )`
    )
    .run();

  await db
    .prepare(
      `INSERT OR IGNORE INTO worker_state (id, last_dispatch_at) VALUES (1, 0)`
    )
    .run();
}

function extractChatId(update) {
  return (
    update?.message?.chat?.id ??
    update?.edited_message?.chat?.id ??
    update?.callback_query?.message?.chat?.id ??
    null
  );
}

async function tgApi(botToken, method, body) {
  const res = await fetch(`https://api.telegram.org/bot${botToken}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function hasActiveWorkflowRun(env) {
  const owner = env.GH_OWNER;
  const repo = env.GH_REPO;
  const workflow = env.GH_WORKFLOW || "bot.yml";
  const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/runs?per_page=5`;
  const headers = {
    Authorization: `Bearer ${env.GH_TOKEN}`,
    Accept: "application/vnd.github+json",
    "User-Agent": "Soft4P-Wake-Worker",
  };

  const res = await fetch(url, { headers });
  if (!res.ok) return false;
  const data = await res.json();
  const runs = data?.workflow_runs || [];
  const nowMs = Date.now();
  const staleLimitMs = 110 * 60 * 1000; // 110 mins (workflow timeout-minutes is 100)

  let active = false;
  for (const r of runs) {
    if (!["queued", "in_progress", "waiting", "pending"].includes(r.status)) {
      continue;
    }
    const createdMs = Date.parse(r.created_at || r.run_started_at || "");
    if (!Number.isNaN(createdMs) && nowMs - createdMs > staleLimitMs) {
      // Cancel stuck/zombie GitHub Actions run so it doesn't block new runs
      try {
        await fetch(
          `https://api.github.com/repos/${owner}/${repo}/actions/runs/${r.id}/cancel`,
          { method: "POST", headers }
        );
      } catch {
        // Ignore cancel failure
      }
      continue;
    }
    active = true;
  }
  return active;
}

async function triggerWorkflow(env) {
  const owner = env.GH_OWNER;
  const repo = env.GH_REPO;
  const workflow = env.GH_WORKFLOW || "bot.yml";
  const branch = env.GH_BRANCH || "main";
  const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "Soft4P-Wake-Worker",
    },
    body: JSON.stringify({ ref: branch }),
  });
  return res.ok;
}

async function ensureRunnerStarted(env) {
  const nowSec = Math.floor(Date.now() / 1000);
  const cutoffSec = nowSec - DISPATCH_COOLDOWN_SEC;

  // Check recent dispatch lock first (prevents duplicate triggers during GitHub's 1-4s queue delay)
  const state = await env.DB.prepare(
    "SELECT last_dispatch_at FROM worker_state WHERE id = 1"
  ).first();
  if (state && state.last_dispatch_at > cutoffSec) {
    return;
  }

  // Check if GitHub Actions already has a queued or running job
  const active = await hasActiveWorkflowRun(env);
  if (active) {
    return;
  }

  // Atomically claim the dispatch lock so two simultaneous messages trigger only ONE runner
  const claim = await env.DB.prepare(
    "UPDATE worker_state SET last_dispatch_at = ? WHERE id = 1 AND last_dispatch_at <= ?"
  )
    .bind(nowSec, cutoffSec)
    .run();

  if (claim?.meta?.changes === 1) {
    const ok = await triggerWorkflow(env);
    if (!ok) {
      // Release lock if dispatch request itself failed
      await env.DB.prepare(
        "UPDATE worker_state SET last_dispatch_at = 0 WHERE id = 1 AND last_dispatch_at = ?"
      )
        .bind(nowSec)
        .run();
    }
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // Health check endpoint
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
      return new Response("Soft4P Wake-on-Message Worker is running.", { status: 200 });
    }

    await ensureSchema(env.DB);

    // Endpoint called by bot.py on GitHub Actions startup to fetch buffered updates
    if (request.method === "POST" && url.pathname === "/pop-updates") {
      const auth = request.headers.get("Authorization") || "";
      if (!env.WORKER_SECRET || auth !== `Bearer ${env.WORKER_SECRET}`) {
        return new Response("Unauthorized", { status: 401 });
      }

      const { results } = await env.DB.prepare(
        "SELECT id, payload, chat_id, status_msg_id FROM pending_updates ORDER BY id ASC"
      ).all();

      if (results && results.length > 0) {
        const maxId = results[results.length - 1].id;
        await env.DB.prepare("DELETE FROM pending_updates WHERE id <= ?")
          .bind(maxId)
          .run();
      }

      const updates = (results || []).map((row) => ({
        update: JSON.parse(row.payload),
        chat_id: row.chat_id,
        status_msg_id: row.status_msg_id,
      }));

      return new Response(JSON.stringify({ updates }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    // Telegram Webhook endpoint
    if (request.method === "POST" && (url.pathname === "/webhook" || url.pathname === "/")) {
      const expectedSecret = env.WEBHOOK_SECRET || env.WORKER_SECRET;
      if (expectedSecret) {
        const tgSecret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
        if (tgSecret !== expectedSecret) {
          return new Response("Forbidden", { status: 403 });
        }
      }

      let update;
      try {
        update = await request.json();
      } catch {
        return new Response("Bad Request", { status: 400 });
      }

      const chatId = extractChatId(update);
      const nowSec = Math.floor(Date.now() / 1000);

      // Insert update into queue
      const insertRes = await env.DB.prepare(
        "INSERT INTO pending_updates (payload, chat_id, status_msg_id, created_at) VALUES (?, ?, NULL, ?)"
      )
        .bind(JSON.stringify(update), chatId, nowSec)
        .run();

      const insertedId = insertRes?.meta?.last_row_id;

      // Get current queue position
      const countRow = await env.DB.prepare(
        "SELECT COUNT(*) AS pos FROM pending_updates"
      ).first();
      const position = countRow?.pos || 1;

      // Acknowledge callback queries immediately so Telegram spinner stops
      if (update?.callback_query?.id) {
        ctx.waitUntil(
          tgApi(env.BOT_TOKEN, "answerCallbackQuery", {
            callback_query_id: update.callback_query.id,
            text: `⏳ Server waking up (Queue #${position})...`,
          })
        );
      }

      // Send friendly queue / wake status message with animated progress bar
      let statusMsgId = null;
      if (chatId && update?.message) {
        try {
          const msgRes = await tgApi(env.BOT_TOKEN, "sendMessage", {
            chat_id: chatId,
            text:
              `⏳ <b>High-Speed Server Waking Up...</b>\n\n` +
              `<code>[▓▓░░░░░░░░] 20%</code> • Queuing request...\n` +
              `📊 Queue Position: <b>#${position}</b>\n\n` +
              `<i>Your download will start automatically as soon as the server comes online.</i>`,
            parse_mode: "HTML",
            reply_to_message_id: update.message.message_id,
          });
          if (msgRes?.ok && msgRes?.result?.message_id && insertedId) {
            statusMsgId = msgRes.result.message_id;
            await env.DB.prepare(
              "UPDATE pending_updates SET status_msg_id = ? WHERE id = ?"
            )
              .bind(statusMsgId, insertedId)
              .run();
          }
        } catch {
          // Non-fatal if status message fails
        }
      }

      // Trigger GitHub Actions only once even if multiple users message simultaneously
      ctx.waitUntil(
        (async () => {
          await ensureRunnerStarted(env);

          if (!statusMsgId || !chatId) return;

          const stages = [
            { delay: 4500, bar: "▓▓▓▓░░░░░░", pct: "40%", label: "Allocating high-speed runner..." },
            { delay: 5000, bar: "▓▓▓▓▓▓░░░░", pct: "60%", label: "Setting up downloader engine..." },
            { delay: 6000, bar: "▓▓▓▓▓▓▓▓░░", pct: "80%", label: "Connecting to Telegram..." },
            { delay: 6000, bar: "▓▓▓▓▓▓▓▓▓░", pct: "95%", label: "Almost ready, starting shortly..." },
          ];

          for (const stage of stages) {
            await new Promise((r) => setTimeout(r, stage.delay));
            try {
              const editRes = await tgApi(env.BOT_TOKEN, "editMessageText", {
                chat_id: chatId,
                message_id: statusMsgId,
                text:
                  `⏳ <b>High-Speed Server Waking Up...</b>\n\n` +
                  `<code>[${stage.bar}] ${stage.pct}</code> • ${stage.label}\n` +
                  `📊 Queue Position: <b>#${position}</b>\n\n` +
                  `<i>Your download will start automatically as soon as the server comes online.</i>`,
                parse_mode: "HTML",
              });
              // Stop animating if bot.py already booted and deleted the status message
              if (!editRes?.ok) break;
            } catch {
              break;
            }
          }
        })()
      );

      return new Response("OK", { status: 200 });
    }

    return new Response("Not Found", { status: 404 });
  },

  // Safety net Cron Trigger (e.g. every 5 mins): restores webhook if runner crashed unexpectedly
  async scheduled(event, env, ctx) {
    try {
      const infoRes = await fetch(
        `https://api.telegram.org/bot${env.BOT_TOKEN}/getWebhookInfo`
      );
      const info = await infoRes.json();
      if (info?.ok && !info?.result?.url) {
        const active = await hasActiveWorkflowRun(env);
        if (!active && env.SELF_WEBHOOK_URL) {
          await tgApi(env.BOT_TOKEN, "setWebhook", {
            url: env.SELF_WEBHOOK_URL,
            secret_token: env.WEBHOOK_SECRET || env.WORKER_SECRET || undefined,
            drop_pending_updates: false,
          });
        }
      }
    } catch {
      // Ignore transient errors
    }
  },
};
