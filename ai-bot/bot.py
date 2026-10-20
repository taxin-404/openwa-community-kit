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
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = pathlib.Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state.json"
PERSONA_FILE = BASE_DIR / "persona.txt"

OPENWA_URL = os.environ.get("OPENWA_URL", "http://localhost:2785/api").rstrip("/")
OPENWA_KEY_FILE = os.environ.get("OPENWA_KEY_FILE", str(BASE_DIR.parent / ".ai-agent-key"))
SESSION_ID_FILE = os.environ.get("SESSION_ID_FILE", str(BASE_DIR.parent / "reminders" / "session-id"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_AUTO_CHAIN = os.environ.get("GEMINI_AUTO_CHAIN", "1") == "1"
_auto_cache: list[str] | None = None
_auto_at = 0.0
GEMINI_SELFTEST = os.environ.get("GEMINI_SELFTEST", "1") == "1"
DEAD_TTL = 86400          # benched models are retried after 24 h
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "6"))
STALE_SECONDS = int(os.environ.get("STALE_SECONDS", "300"))      # skip messages older than this
COOLDOWN_SECONDS = int(os.environ.get("REPLY_COOLDOWN_SECONDS", "45"))
CONTEXT_MESSAGES = int(os.environ.get("CONTEXT_MESSAGES", "10"))
# 0 = answer every DM (even a lone "." ping) — never leave someone unread.
# Set to 2 or higher to silently ignore dot/emoji-only pings.
MIN_MESSAGE_CHARS = int(os.environ.get("MIN_MESSAGE_CHARS", "0"))
# Sent instead of silence when every AI model fails (quotas dry, network down).
FALLBACK_TEXT = os.environ.get(
    "FALLBACK_TEXT",
    "🤖 (auto-reply) ami ekhon answer dite parchi na — ektu por abar likhben!")
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


def auto_chain(key: str) -> list[str]:
    """Discover every chat-capable Gemini model on the key, cheapest (lites) first."""
    global _auto_cache, _auto_at
    now = time.time()
    if _auto_cache is not None and now - _auto_at < 86400:
        return _auto_cache
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
            headers={"x-goog-api-key": GEMINI_API_KEY},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        names = set()
        for m in data.get("models", []):
            name = (m.get("name") or "").split("/")[-1]
            methods = m.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            if not name.startswith("gemini-"):
                continue
            if re.search(r"tts|live|audio|image|omni|banana|computer-use|robotics|customtools|transcribe|thinking|embedding|latest", name, re.I):
                continue
            names.add(name)
        def rank(n: str):
            v = re.search(r"(\d+)(?:\.(\d+))?", n)
            ver = (float(v.group(1)) + float(v.group(2) or 0) / 10) if v else 0.0
            group = 0 if "lite" in n else (2 if "pro" in n else 1)
            return (group, -ver)
        _auto_cache = sorted(names, key=rank)
        _auto_at = now
        log(f"model chain discovered ({len(_auto_cache)}): {', '.join(_auto_cache[:5])} …")
    except Exception as exc:                                 # noqa: BLE001
        log(f"model discovery failed ({exc}) — using manual list for now")
        _auto_at = now - 1800        # retry discovery in ~30 min
    return _auto_cache or []


def selftest_models(models: list[str], state: dict, persist: bool = True) -> None:
    """Probe every discovered model once at boot; bench the dead ones for 24 h.

    A bench verdict is a hard "not available on this key": 404/403 (gated,
    e.g. 'no longer available to new users') or a quota error that says
    `limit: 0`. Transient failures (5xx overload, network) keep the model
    alive — the chain hops those anyway during the day. Benched models are
    re-probed on every restart, so healing is automatic."""
    def probe(name: str):
        try:
            gemini_call(name, "", "Reply with exactly: OK")
            return name, None
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode(errors="ignore")[:2000]
            except Exception:                                # noqa: BLE001
                pass
            if exc.code in (404, 403) or "limit: 0" in body:
                return name, f"HTTP {exc.code}"
            return name, None
        except Exception as exc:                             # noqa: BLE001
            return name, None
    benched: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(probe, m) for m in models]
        for fut in as_completed(futs):
            name, why = fut.result()
            if why:
                benched[name] = why
    now = time.time()
    fresh_dead = {m: t for m, t in state.get("dead_models", {}).items()
                  if now - t < DEAD_TTL}
    for m in benched:
        fresh_dead[m] = now
    state["dead_models"] = fresh_dead
    if persist:
        save_state(state)
    alive = len(models) - len(benched)
    if benched:
        log(f"self-test: {alive} alive, benched for 24 h: {', '.join(benched)}")
    else:
        log(f"self-test: {alive} alive")


def ai_reply(persona: str, prompt: str, state: dict, persist: bool = True) -> str:
    """One-by-one: drain each model's daily quota before moving to the next.

    429-per-day and unavailable (404/403) models advance a persisted cursor;
    transient errors (5xx/timeouts, 429-per-minute) are skipped for just this
    message. When the last model dies too, the cursor wraps back to the first
    (earlier buckets refilled overnight)."""
    models = [GEMINI_MODEL.strip()]
    if GEMINI_AUTO_CHAIN:
        for m in auto_chain(GEMINI_API_KEY):
            if m not in models:
                models.append(m)
    if not GEMINI_AUTO_CHAIN or not _auto_cache:
        raw = os.environ.get("GEMINI_MODEL_FALLBACKS", "") or os.environ.get("GEMINI_MODEL_FALLBACK", "")
        for m in raw.split(","):
            m = m.strip()
            if m and m not in models:
                models.append(m)
    dead = {m: t for m, t in state.get("dead_models", {}).items()
            if time.time() - t < DEAD_TTL}
    usable = [m for m in models if m not in dead]
    if usable:                                              # try benched
        models = usable                                   # only if all else gone
    models = models[:14]

    cursor = min(int(state.get("model_cursor", 0)), len(models) - 1)
    last_exc: Exception = RuntimeError("no LLM model configured")
    for i in range(cursor, len(models)):
        model = models[i]
        try:
            out = gemini_call(model, persona, prompt)
            state["model_cursor"] = i
            if persist:
                save_state(state)
            if i != cursor:
                log(f"switched → {model} (position {i}/{len(models) - 1})")
            return out
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode(errors="ignore")[:2000]
            except Exception:                                # noqa: BLE001
                pass
            if exc.code in (404, 403) or "limit: 0" in body:
                log(f"{model}: not available on this key → benched for 24 h, next model")
                dead = state.setdefault("dead_models", {})
                dead[model] = time.time()
                if persist:
                    save_state(state)
            elif "PerDay" in body or (exc.code == 429 and "PerMinute" not in body):
                log(f"{model}: daily bucket finished → next model")
                if i + 1 < len(models):
                    state["model_cursor"] = i + 1
                    if persist:
                        save_state(state)
                else:
                    state["model_cursor"] = 0                 # wrap around
                    if persist:
                        save_state(state)
            else:
                log(f"{model}: transient error (HTTP {exc.code}) — skipping for this message")
            last_exc = exc
        except Exception as exc:                             # noqa: BLE001
            log(f"{model}: transient error ({exc}) — skipping for this message")
            last_exc = exc
    raise last_exc


def gemini(persona: str, prompt: str) -> str:
    """Cheap test-path wrapper: walk the chain without touching the cursor."""
    return ai_reply(persona, prompt, {}, persist=False)


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
        if len(re.findall(r"[^\W_]", body, re.UNICODE)) < MIN_MESSAGE_CHARS:
            log(f"skip {chat} (punctuation-only / too short) wa={wid[:24]}")
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
            reply = ai_reply(persona, prompt, state)
        except Exception as exc:                            # noqa: BLE001
            log(f"LLM error for {chat}: {exc} — sending fallback text")
            reply = FALLBACK_TEXT
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
    if GEMINI_SELFTEST and GEMINI_API_KEY:
        boot_chain = [GEMINI_MODEL.strip()]
        boot_chain += [m for m in auto_chain(GEMINI_API_KEY) if m not in boot_chain]
        if _auto_cache:
            selftest_models(boot_chain, state)
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
