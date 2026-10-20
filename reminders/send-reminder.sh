#!/usr/bin/env bash
# Send message.txt to every contact in contacts.txt via OpenWA bulk send.
# Usage:  ./send-reminder.sh            (from anywhere — paths resolve to this folder)
set -euo pipefail
cd "$(dirname "$0")"

OPENWA_URL="${OPENWA_URL:-http://localhost:2785/api}"
KEY_FILE="${KEY_FILE:-../.api-key}"

fail() { echo "❌ $*" >&2; exit 1; }
[[ -s $KEY_FILE ]] || fail "API key not found at $KEY_FILE"
[[ -s session-id ]] || fail "session-id is empty — put your session's id in reminders/session-id (see README.md)"
[[ -s contacts.txt ]] || fail "contacts.txt has no contacts"
[[ -s message.txt ]] || fail "message.txt is empty"

API_KEY="$(cat "$KEY_FILE")"
SESSION_ID="$(cat session-id)"

# ---- build JSON payloads (OpenWA max 100 messages per batch) -----------------
python3 - <<'PY' || fail "failed to build payloads from contacts.txt"
import json, pathlib, re

base = pathlib.Path('.')
text = (base / 'message.txt').read_text(encoding='utf-8').strip()

contacts = []
for line in (base / 'contacts.txt').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    phone, _, name = line.partition('|')
    phone = re.sub(r'[^0-9@]', '', phone.strip().lstrip('+'))
    if not phone:
        print(f'  ⚠️  skipping unreadable line: {line!r}', flush=True)
        continue
    item = {'chatId': phone if '@' in phone else phone + '@c.us',
            'type': 'text', 'content': {'text': text}}
    name = name.strip()
    if name:
        item['variables'] = {'name': name}
    contacts.append(item)

if not contacts:
    raise SystemExit(1)

for f in base.glob('payload-chunk-*.json'):
    f.unlink()

per = 100
chunks = [contacts[i:i + per] for i in range(0, len(contacts), per)]
(base / 'chunks.txt').write_text(
    ''.join(f'payload-chunk-{i + 1:03d}.json\n' for i in range(len(chunks))))
for i, c in enumerate(chunks, 1):
    (base / f'payload-chunk-{i:03d}.json').write_text(
        json.dumps({'messages': c, 'options': {'stopOnError': False}}, ensure_ascii=False))
print(f'📋 {len(contacts)} contact(s) → {len(chunks)} batch(es)')
PY

# ---- send each batch ---------------------------------------------------------
while read -r chunk; do
  echo "📤 sending $chunk ..."
  http="$(curl -sS -o last-response.json -w '%{http_code}' \
    -X POST "$OPENWA_URL/sessions/$SESSION_ID/messages/send-bulk" \
    -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
    -d @"$chunk")" || fail "could not reach OpenWA at $OPENWA_URL"
  [[ $http == 202 ]] || fail "batch rejected (HTTP $http): $(cat last-response.json)"
  jq -r '"   ✅ batch \(.batchId) queued — \(.totalMessages) message(s), statusUrl: \(.statusUrl)"' last-response.json
  [[ $(wc -l < chunks.txt) -gt 1 ]] && sleep 5
done < chunks.txt

echo "👉 check delivery later with:"
echo "   curl -s -H \"X-API-Key: \$API_KEY\" \"$OPENWA_URL/sessions/$SESSION_ID/messages/batch/<batchId>\""
