#!/usr/bin/env python3
"""OpenWA AI auto-reply bot (Gemini).

Polls the OpenWA message store for new incoming messages, builds a short
conversation context per chat and asks Gemini for the reply, then sends it
back through OpenWA. Pure standard library.

Run by:  systemctl --user status openwa-ai-bot     (service file provided)
Manual:  python3 bot.py test "why is the building closed"   -> prints answer only
"""

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = pathlib.Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state.json"
PERSONA_FILE = BASE_DIR / "persona.txt"

OPENWA_URL = os.environ.get("OPENWA_URL", "http://localhost:2785/api").rstrip("/")
OPENWA_KEY_FILE = os.environ.get("OPENWA_KEY_FILE", str(BASE_DIR.parent / ".ai-agent-key"))
SESSION_ID_FILE = os.environ.get("SESSION_ID_FILE", str(BASE_DIR.parent / "reminders" / "session-id"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "6"))
STALE_SECONDS = int(os.environ.get("STALE_SECONDS", "300"))      # skip messages older than this
COOLDOWN_SECONDS = int(os.environ.get("REPLY_COOLDOWN_SECONDS", "45"))
CONTEXT_MESSAGES = int(os.environ.get("CONTEXT_MESSAGES", "10"))
ANSWER_GROUPS = os.environ.get("ANSWER_GROUPS", "0") == "1"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8").strip()

def openwa(key: str, method: str, path: str, payload: dict | None = None, timeout: int = 30):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    if body is not None:
        headers["Content-Length"] = str(len(body))
    req = urllib.request.Request(
        f"{OPENWA_URL}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def gemini_call(model: str, persona: str, prompt: str) -> str:
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": persona}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 512},
    }).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=body,
        method="POST",
        headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    parts = ""
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            parts += part.get("text", "")
    if not parts:
        raise RuntimeError(f"Gemini returned no text: {json.dumps(data)[:300]}")
    return parts.strip()


def gemini(persona: str, prompt: str) -> str:
    """Try GEMINI_MODEL first, fall back to GEMINI_MODEL_FALLBACK on any error."""
    models = [GEMINI_MODEL.strip()]
    fallback = os.environ.get("GEMINI_MODEL_FALLBACK", "").strip()
    if fallback and fallback not in models:
        models.append(fallback)
    last_exc: Exception = RuntimeError("no model configured")
    for model in models:
        try:
            out = gemini_call(model, persona, prompt)
            if model != models[0]:
                log(f"primary model failed → used fallback {model}")
            return out
        except Exception as exc:                             # noqa: BLE001
            last_exc = exc
            log(f"model {model} failed: {exc}")
    raise last_exc


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen": {}, "last_reply": {}}

def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE_FILE)


def seed_seen(key: str, sid: str, state: dict) -> None:
    """First run: mark every existing incoming message as handled (no flood)."""
    data = openwa(key, "GET", f"/sessions/{sid}/messages?direction=incoming&limit=100&inlineMedia=false")
    for m in data.get("messages", []):
        if m.get("waMessageId"):
            state["seen"][m["waMessageId"]] = int(time.time())


def process(key: str, sid: str, state: dict) -> None:
    persona = read(PERSONA_FILE)
    now = int(time.time())
    # Newest-first incoming page
    data = openwa(key, "GET", f"/sessions/{sid}/messages?direction=incoming&orderBy=timestamp&limit=20&inlineMedia=false")
    for m in reversed(data.get("messages", [])):          # oldest first
        wid = m.get("waMessageId")
        body = (m.get("body") or "").strip()
        chat = m.get("chatId") or ""
        if not wid or wid in state["seen"]:
            continue
        state["seen"][wid] = now
        ts = m.get("timestamp") or 0
        stale = now - ts > STALE_SECONDS
        group = chat.endswith("@g.us")
        answerable = bool(body) and not stale and (ANSWER_GROUPS or not group) \
            and "status@broadcast" not in chat and "@broadcast" not in chat
        save_state(state)
        if not answerable:
            reason = "older than STALE_SECONDS" if stale else \
                     "group chat" if group else \
                     "media/empty body" if not body else "broadcast/status"
            log(f"skip {chat} ({reason}) wa={wid[:24]}")
            continue
        if now < state["last_reply"].get(chat, 0) + COOLDOWN_SECONDS:
            log(f"cooldown {chat} — skipping auto-reply this round")
            continue
        # --- context -------------------------------------------------------
        ctx = openwa(key, "GET", "/".join([
            f"/sessions/{sid}/messages",
            f"?chatId={urllib.parse.quote(chat)}&inlineMedia=false&limit={CONTEXT_MESSAGES}",
        ]))
        lines = []
        for c in reversed(ctx.get("messages", [])):
            who = "member" if c.get("direction") == "incoming" else "assistant"
            body_c = (c.get("body") or "").strip()
            if body_c:
                lines.append(f"{who}: {body_c}")
        transcript = "\n".join(lines[-CONTEXT_MESSAGES:]) or f"member: {body}"
        prompt = f"Conversation with community member {m.get('chatName') or m.get('from')}:\n\n{transcript}\n\nWrite ONLY the next assistant reply."
        # --- think ---------------------------------------------------------
        try:
            reply = gemini(persona, prompt)
        except Exception as exc:                            # noqa: BLE001
            log(f"Gemini error for {chat}: {exc}")
            continue
        # --- send ----------------------------------------------------------
        try:
            openwa(key, "POST", f"/sessions/{sid}/messages/send-text", {"chatId": chat, "text": reply})
        except urllib.error.HTTPError as exc:
            log(f"send failed for {chat}: HTTP {exc.code} {exc.read()[:200]!r}")
            continue
        state["last_reply"][chat] = now
        save_state(state)
        log(f"replied {chat} → {reply[:60]!r}")


def run_forever() -> None:
    if not GEMINI_API_KEY:
        log("GEMINI_API_KEY is empty — idling. Paste it into ai-bot.env, then: "
            "systemctl --user restart openwa-ai-bot")
    key = read(pathlib.Path(OPENWA_KEY_FILE).expanduser())
    sid = read(pathlib.Path(SESSION_ID_FILE).expanduser())
    state = load_state()
    if not state["seen"]:
        seed_seen(key, sid, state)
        save_state(state)
        log(f"seeded {len(state['seen'])} existing message(s) as seen")
    log(f"watching session {sid} every {POLL_SECONDS}s (model {GEMINI_MODEL})")
    while True:
        try:
            if GEMINI_API_KEY:
                process(key, sid, state)
        except Exception as exc:                            # noqa: BLE001
            log(f"cycle error: {exc}")
        now = int(time.time())
        if len(state["seen"]) > 5000:                       # prune week-old ids
            state["seen"] = {k: v for k, v in state["seen"].items() if now - v < 7 * 86400}
        time.sleep(POLL_SECONDS)


def test(prompt: str) -> None:
    out = gemini(read(PERSONA_FILE), f"Conversation:\nmember: {prompt}\n\nWrite ONLY the assistant reply.")
    print(out)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        if not GEMINI_API_KEY:
            sys.exit("GEMINI_API_KEY missing — paste it into ai-bot.env first")
        test(" ".join(sys.argv[2:]) or "hello?")
    else:
        run_forever()
