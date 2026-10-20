# AGENTS.md — one-prompt WhatsApp community setup (for coding agents)

A user opened this repo and asked you to do the setup FOR them.
Your job: bring up a complete working stack (OpenWA gateway → WhatsApp session
linked → monthly-reminder script → Gemini auto-reply bot → optional AI-assistant
MCP) on this Linux machine, minimizing manual steps and never leaking secrets.

Act methodically, verify every state before moving on, and follow the rules
below exactly.

## Non-negotiable rules

1. **Secrets stay invisible.** Never print an API key, Gemini key, or session
   id into the conversation, logs, or command output. Keys live in files with
   `chmod 400`/`600`. Reference them as `$(cat path)` in shell commands.
2. **No autonomous sudo.** If a step needs root or the `docker` group, print the
   exact one-line command for the user to run themselves, explain why, and wait
   for them to confirm.
3. **Idempotent.** Detect what already exists before acting: a running container
   (`curl -s -m 3 http://localhost:2785/api/health`), an existing `~/openwa`,
   existing key files, existing `session-id`. Skip what works.
4. **Confirm before destructive changes** (composing down, deleting files that
   hold data, overwriting another agent's config beyond a careful merge).
5. **Never connect the user's personal number** — recommend a dedicated number
   and say why (unofficial automation ~ ban risk). Telegram-style compliance:
   if they insist on a regulated commercial use, point to Meta's official
   Cloud API.
6. Ask a question when a human is genuinely needed and STOP — one ask per gate,
   with the exact copy-pasteable command.

## Checklist

### Step 0 — environment check
- Verify: `git`, `curl`, `jq`, `python3`, `docker compose version`.
- Missing pieces: offer the package-manager install command for their distro
  (does `pacman --version` or `apt --version` run?) and ask before installing.

### Step 1 — OpenWA gateway
- If `~/openwa/docker-compose.dev.yml` missing:
  `git clone --depth 1 https://github.com/rmyndharis/OpenWA.git ~/openwa`
- Start: `cd ~/openwa && docker compose -f docker-compose.dev.yml up -d --build`
  (background it; builds take minutes).
- Docker *permission denied* on the socket → print for the user:
  `sudo usermod -aG docker "$USER"` then tell them to fully log out/in or run
  it through `su - "$USER" -c '…up -d…'`. Old shells keep stale groups.
- Healthy when: `curl -s http://localhost:2785/api/health` returns `{"status":"ok"...}`.
- On first boot, print this for the user to run (sudo):
  ```bash
  sudo sh -c 'cat ~/openwa/data/.api-key > ~/openwa/.api-key && chown "$USER:$USER" ~/openwa/.api-key && chmod 400 ~/openwa/.api-key'
  ```
  Verify afterwards with `[[ -s ~/openwa/.api-key ]]` — do not `cat` it.

### Step 2 — link the WhatsApp session
- If no session exists yet, create + start one via the API:
  ```bash
  KEY=$(cat ~/openwa/.api-key)
  RESP=$(curl -sS -X POST http://localhost:2785/api/sessions \
    -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
    -d '{"name":"community"}')
  ```
  Then `POST /api/sessions/{id}/start`.
- Grab the QR while status is `qr_ready`:
  `GET /api/sessions/{id}/qr` returns `qrCode` as a **base64 PNG data-URI** —
  strip the prefix, `base64 -d`, save as a PNG, and ALSO render it in chat as
  an image so the user can scan immediately. QRs expire fast (~20–60 s) —
  offer "new qr" regeneration.
- Ask the user to scan with a **dedicated number**; WAIT for "linked".
- Verify: `GET /api/sessions/{id}` → `status: "ready"` with `phone` filled.
- Write the session UUID into `~/openwa/reminders/session-id` (chmod 600).
- **If the phone answers "can't link" / shows a passkey prompt:** this is the
  WhatsApp server-side gate (OpenWA issue #560) — regenerate a fresh QR once;
  if it persists, the account cannot be linked by ANY unofficial gateway; tell
  the user their options (different SIM, official Cloud API, wait upstream).

### Step 3 — mint the least-privilege key for the AI bot
- POST `/api/auth/api-keys` with body
  `{"name":"ai-agent","role":"operator","allowedSessions":["<session-uuid>"]}`
  using the ADMIN key. The response contains `apiKey` **exactly once** —
  write it directly to `~/openwa/.ai-agent-key` (chmod 400) with `jq -r '.apiKey'`
  piped to a file; never echo it.

### Step 4 — install the kit files
- `./install.sh` from this repo (default target `~/openwa`). It copies
  `reminders/`, `ai-bot/bot.py`, the systemd unit, and enables it.

### Step 5 — Gemini key + bot activation
- Ask the user for their Google AI Studio key (https://aistudio.google.com/apikey).
  Fastest safe flow: have THEM paste it into `~/openwa/ai-bot/ai-bot.env` behind
  `GEMINI_API_KEY=` themselves (or run: `systemctl --user`-style commands).
  Alternatively accept it in chat and write it straight into the file — but
  never repeat it back afterwards.
- Ensure the file holds real home paths and this model chain:
  `GEMINI_MODEL=gemini-3.5-flash-lite` and
  `GEMINI_MODEL_FALLBACKS=gemini-3.1-flash-lite,gemini-3.6-flash,gemini-2.5-flash,gemini-2.5-flash-lite`
  (comma chain; each model has its own daily free-tier RPD bucket, and a 429 or
  404 on one model hands work to the next automatically).
- `python3 -m py_compile ~/openwa/ai-bot/bot.py`, then
  `systemctl --user restart openwa-ai-bot`, then verify with
  `python3 ~/openwa/ai-bot/bot.py test "hello?"` — it prints an answer and
  sends nothing.

### Step 6 — verify end-to-end (no third party needed)
- Send the user's own number a test reminder through
  `reminders/send-reminder.sh` after adding a line
  `<their-number>|Test` to `contacts.txt` — it lands in their own
  "message yourself" chat. Confirm `202` + batch status `completed`.
- The auto-reply path is verified when a REAL incoming DM appears
  (`journalctl --user -u openwa-ai-bot -f` shows `replied …`). If new messages
  never reach the store while the session is `ready`, the live stream has
  stalled: `POST …/stop` then `POST …/start` re-syncs it — no re-pairing.

### Step 7 — optional: AI-assistant control (MCP)
- Append to `~/openwa/.env` (create if absent):
  ```
  MCP_ENABLED=true
  MCP_READONLY=false
  ```
- The OPERATOR key from Step 3 is exactly what assistants should use.
- If the user wants OpenCode/coding-agent control: merge into
  `~/.config/opencode/opencode.json` (prefer `jq` over string surgery; keep a
  `.backup` of the original):
  ```json
  { "mcp": { "openwa": { "type": "remote", "url": "http://localhost:2785/mcp",
    "enabled": true, "oauth": false,
    "headers": { "Authorization": "Bearer <OPERATOR_KEY>" } } } }
  ```
  (chmod 600 afterwards). Tell them the tools appear in a NEW session.

### Step 8 — report
Produce a final summary table: gateway, session (phone/name), bot service state,
file paths for every secret, the monthly-cron line to add when ready, and
"restart the session" as the known remedy for a stalled stream.

## Exact human gates (STOP and ask, then continue)
1. sudo command for the admin-key copy (Step 1)
2. QR scan with a dedicated number (Step 2)
3. Gemini key paste (Step 5)
4. Everything else: do it yourself.
