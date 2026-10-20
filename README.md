# OpenWA Community Kit

> 🤖 **Don't want to do this manually?** Open any AI coding agent (OpenCode /
> Claude Code / …), copy the prompt below, and paste it. The agent clones this
> repo itself, then performs the whole setup — stopping only at the 3 gates
> that need a human: a sudo command, a WhatsApp QR scan, and the Gemini API key.

```text
Set up my self-hosted WhatsApp community kit. First: git clone https://github.com/taxin-404/openwa-community-kit.git and cd into that folder. Then open AGENTS.md, read it fully, and follow it exactly, in order: install and start OpenWA in Docker, create the WhatsApp session, generate and show me the QR code to link my phone, mint the least-privilege OPERATOR API key, run install.sh, configure the Gemini auto-reply bot (gemini-3.5-flash-lite with gemini-3.1-flash-lite fallback), enable the systemd service, and verify everything works. Only stop and ask me at the three human gates: the sudo command, the QR scan, and pasting my Gemini API key.
```

Self-hosted WhatsApp for a small community: **monthly reminders to a list of
contacts** + an **AI (Gemini) auto-reply** for direct messages when you're busy,
plus an optional **AI-assistant hook** so an agent (Claude, OpenCode, …) can
drive the gateway.

```
                    ┌────────────────────────── OpenWA (Docker) ─────────────────────┐
 member phones ───▶│ session storage ◀── REST API :2785 ── MCP /api/mcp ── dashboard │
                    └───────▲───────────────────▲──────────────────▲────────────────┘
                            │                   │                  │
      send text (calls) ────┘   poll & reply ───┘        tools ────┘
                            │                   │
                  reminders/send-reminder.sh   ai-bot/bot.py  (Gemini 3.5-lite
                          + cron (monthly)      → fallback 3.1-lite, systemd)
```

Everything runs on **one Linux box with Docker**. Nothing is exposed beyond
`127.0.0.1`.

> ⚠️ **Read first — unofficial automation.** OpenWA talks to WhatsApp through
> reverse-engineered clients, *not* Meta's Cloud API. There is always a nonzero
> risk of account restriction/ban: use a **dedicated number** you can afford to
> lose, send only to people who expect messages, keep volumes modest, and keep
> an SMS/email fallback for anything critical. For regulated/official use, use
> Meta's official Cloud API instead.

---

## 1. Prerequisites

- Linux + **Docker** with `docker compose` (and your user in the `docker` group)
- `git`, `curl`, `jq`, `python3`
- A free **Google AI Studio key**: https://aistudio.google.com/apikey

## 2. Install OpenWA

```bash
git clone --depth 1 https://github.com/rmyndharis/OpenWA.git ~/openwa
cd ~/openwa && docker compose -f docker-compose.dev.yml up -d --build
```

First boot writes an **admin API key**:

```bash
sudo sh -c 'cat ~/openwa/data/.api-key > ~/openwa/.api-key && chown "$USER:$USER" ~/openwa/.api-key && chmod 400 ~/openwa/.api-key'
```

Dashboard: http://localhost:2785 (log in with that key).

## 3. Link the WhatsApp session

1. On the phone (**dedicated number preferred**): WhatsApp → Settings →
   **Linked devices** → Link a device
2. In the dashboard create a session (e.g. name `community`), press start, scan
   the QR. The QR rotates every ~30 s — regenerate rather than re-scan an old one.

If linking fails:
- *"can't link / create a passkey" prompt* → a WhatsApp server-side gate on some
  accounts (OpenWA issue #560); no unofficial gateway can link such an account
  yet. Try a different SIM instead.
- Messages suddenly stop arriving (your phone shows them, the gateway doesn't) →
  **restart the session** (stop + start in the dashboard, no re-pairing) — the
  live event stream sometimes stalls after linking.

Save the session id (UUID, shown in the dashboard) for later:
```bash
echo PASTE-SESSION-UUID > ~/openwa/reminders/session-id   # dir created in step 4
```

## 4. Install this kit

**With an AI agent (recommended):** open the cloned folder with your coding
agent (OpenCode, Claude Code, …) and say *"follow AGENTS.md and set everything
up for me"* — the agent handles every step below and pauses only at the three
human gates (sudo password, QR scan, Gemini key).

**Manually:**

```bash
git clone https://github.com/<you>/openwa-kit.git ~/openwa-kit
cd ~/openwa-kit
./install.sh            # installs into ~/openwa by default
```

## 5. Reminder (cron-ready, scheduled later)

| File | Purpose |
|---|---|
| `reminders/contacts.txt` | community numbers, one per line: `8801xxxxxxx\|Amina` |
| `reminders/message.txt` | the text to send; `{{name}}` supported |
| `send-reminder.sh` | sends via OpenWA bulk (spread ~3 s apart, >100 auto-chunked) |

```bash
~/openwa/reminders/send-reminder.sh
```

Once a month at 10:00 (`crontab -e`):
```
0 10 1 * * /home/YOU/openwa/reminders/send-reminder.sh >> /home/YOU/openwa/reminders/cron.log 2>&1
```
Cron uses local machine time; a run is **skipped** if the machine was off.

> Message **only people who opted in**, and prefer small monthly audience —
> unsolicited blasts are the #1 ban trigger.

## 6. AI auto-reply (Gemini, free tier)

`ai-bot/bot.py` polls the OpenWA message store, gives Gemini the last 10
messages of the chat as context, and sends the reply back. Guards:

- **DMs only** (`ANSWER_GROUPS=0`), 45 s per-chat cooldown, skips media/sticker
  texts, skips anything older than 60 min, first boot marks history as seen
- **Model fallback chain**: tries `gemini-3.5-flash-lite`, and on *any* error —
  including the daily free-tier RPD quota (HTTP 429) — walks
  `GEMINI_MODEL_FALLBACKS` (default `3.1-flash-lite, 3.6-flash, 2.5-flash, 2.5-flash-lite`).
  Each model has its own daily RPD bucket, and restricted/unknown models in
  the chain are skipped silently.
- Never sends anything if Gemini fails; it just retries on the next message.

Steps: create your OpenWA **OPERATOR key** scoped to the one session

```bash
curl -X POST http://localhost:2785/api/auth/api-keys \
  -H "X-API-Key: $(cat ~/openwa/.api-key)" -H "Content-Type: application/json" \
  -d '{"name":"ai-agent","role":"operator","allowedSessions":["PASTE-SESSION-UUID"]}'
# save the returned apiKey field:  cat > ~/openwa/.ai-agent-key && chmod 400 ~/openwa/.ai-agent-key
```

then fill `ai-bot/ai-bot.env` (`GEMINI_API_KEY=…`, real home paths) and:

```bash
systemctl --user restart openwa-ai-bot
python3 ~/openwa/ai-bot/bot.py test "when is the meeting?"   # answer printed, nothing sent
```

Tune the wit/personality in `ai-bot/persona.txt` (Bangla + English friendly).

## 7. Optional: AI assistant gets tools (MCP)

```
MCP_ENABLED=true
MCP_READONLY=false        # read+write tier for the assistant
```
in `~/openwa/.env`, then `docker compose -f docker-compose.dev.yml up -d`
(recreates the container). The OPERATOR key above is what assistants should
use — least privilege, one session, no IP allowlist. Keep `/mcp` off the
public internet (it's on 127.0.0.1 by default).

OpenCode example (`~/.config/opencode/opencode.json`):
```json
{ "mcp": { "openwa": { "type": "remote", "url": "http://localhost:2785/mcp",
  "enabled": true, "headers": { "Authorization": "Bearer <OPERATOR_KEY>" },
  "oauth": false } } }
```
MCP tools load when an OpenCode session starts.

## 8. Operations cheat-sheet

| Task | Command |
|---|---|
| Gateway up/down | `docker compose -f ~/openwa/docker-compose.dev.yml up -d` |
| Restart session (fix stalled messages) | dashboard ▸ session ▸ stop ▸ start |
| Bot status / logs | `systemctl --user status openwa-ai-bot` |
| Bot dry-run (no send) | `python3 ~/openwa/ai-bot/bot.py test "hi"` |
| Send a reminder | `~/openwa/reminders/send-reminder.sh` |
| Check a bulk batch | `curl -s -H "X-API-Key: $(cat ~/openwa/.api-key)" localhost:2785/api/sessions/$(cat ~/openwa/reminders/session-id)/messages/batch/<batchId>` |

**Secrets policy:** `.api-key`, `.ai-agent-key`, `ai-bot.env`, `contacts.txt`,
`session-id`, `state.json` and QR images are git-ignored — this repo ships only
`.example` templates. Never commit real values.

## 9. License

MIT — see `LICENSE`. Not affiliated with Meta/WhatsApp; trademark terms of
their owners. Use at your own risk and within WhatsApp's terms.
