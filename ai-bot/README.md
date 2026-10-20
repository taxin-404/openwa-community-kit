# AI auto-reply bot

Members message your WhatsApp → Gemini thinks → reply goes out. Runs only while
this PC is on; the systemd service keeps it alive and starts it at login.

```
member message (WhatsApp)
   └─ OpenWA stores it
        └─ bot polls every 6 s, builds last 10 messages of that chat
             └─ Gemini (free tier) writes a reply
                  └─ bot sends it back via OpenWA
```

Safety rails built in:
- replies only to **direct chats** (`ANSWER_GROUPS=0` in `ai-bot.env`)
- **45 s cooldown** per chat so it can never spam
- skips messages **older than 60 min** (no burst after restart) and your own sends
- first boot marks history as seen instead of answering everything at once
- **model fallback chain**: primary `gemini-3.5-flash-lite`, then any failure —
  including the daily free-tier RPD quota (HTTP 429) — walks
  `GEMINI_MODEL_FALLBACKS` (default `3.1-flash-lite, 3.6-flash, 2.5-flash-lite`);
  each model owns a separate RPD bucket, restricted/unknown ones are skipped

## Turn it on
1. Get a free key: https://aistudio.google.com/apikey
2. Paste it into `ai-bot.env` after `GEMINI_API_KEY=`
3. `systemctl --user restart openwa-ai-bot`

## Everyday controls
| Do this | Command |
|---|---|
| status / logs | `systemctl --user status openwa-ai-bot` |
| stop auto-replies | `systemctl --user stop openwa-ai-bot` |
| start again | `systemctl --user start openwa-ai-bot` |
| test without sending | `python3 ~/openwa/ai-bot/bot.py test "when is the meeting?"` |
| change the AI's personality | edit `persona.txt`, then restart |
| let it answer groups too | `ANSWER_GROUPS=1` in `ai-bot.env`, restart |
