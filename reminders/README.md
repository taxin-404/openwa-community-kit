# Community reminder — how it works

```
send-reminder.sh  ←  run manually now; hook it to cron later
   └─ reads   contacts.txt   phone numbers (+ optional |Name)
              message.txt    the reminder text ({{name}} supported)
              session-id     the OpenWA session to send from
   └─ calls   OpenWA  POST /sessions/{id}/messages/send-bulk
              (spaced ~3 s apart with a randomized pause — WhatsApp-friendly)
```

## 1. Fill in the contacts
Edit `contacts.txt`, one per line:

```
8801712345678|Amina
6281234567890|Fahim
```

Phone = country code + number, digits only (no `+`, no spaces). Only people
who expect messages from you (your community members) belong in this list.

## 2. Write the reminder
Edit `message.txt`. You can use `{{name}}` in the text, but only if **every**
line in contacts.txt has a `|Name` — otherwise the literal `{{name}}` appears.

## 3. Send a test
```bash
~/openwa/reminders/send-reminder.sh
```
Each run creates a batch, prints its `batchId`, and returns immediately.
Progress:

```bash
curl -s -H "X-API-Key: $(cat ~/openwa/.api-key)" \
  http://localhost:2785/api/sessions/$(cat ~/openwa/reminders/session-id)/messages/batch/<batchId>
```

`last-response.json` keeps the last status response.

## 4. Once a month (when you are ready)
Add one crontab line (`crontab -e`) — this example: 1st of every month at 10:00:

```
0 10 1 * * /home/taxin/openwa/reminders/send-reminder.sh >> /home/taxin/openwa/reminders/cron.log 2>&1
```

Cron uses this machine's local time (+06). If the machine is off/asleep at
that moment, the run is **skipped** — start it manually right after.

## Getting the session id
In the dashboard (http://localhost:2785) open the session's details — the id
is a UUID — and paste it into this folder's `session-id` file:

```bash
echo PASTE-ID-HERE > ~/openwa/reminders/session-id
```

## Gotchas
- OpenWA must be running (`docker compose -f ~/openwa/docker-compose.dev.yml up -d`).
- A number that isn't on WhatsApp fails as one item; the rest of the batch still sends.
- Max 100 numbers per batch — the script splits longer lists automatically.
- Unofficial automation always carries some account risk; a monthly reminder to
  people who already know you is the low-risk kind of send.
