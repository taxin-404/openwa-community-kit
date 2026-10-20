#!/usr/bin/env bash
# Install this kit onto an OpenWA deployment.
# Usage:   ./install.sh [OPENWA_DIR]      (default: ~/openwa)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OPENWA_DIR="${1:-$HOME/openwa}"

fail() { echo "❌ $*" >&2; exit 1; }
say()  { echo "🔹 $*"; }

[[ -f "$OPENWA_DIR/docker-compose.dev.yml" ]] || fail "$OPENWA_DIR has no docker-compose.dev.yml — install OpenWA first (see README step 2)"
say "installing into $OPENWA_DIR"

# --- reminders ----------------------------------------------------------------
mkdir -p "$OPENWA_DIR/reminders"
cp -f "$ROOT/reminders/send-reminder.sh" "$OPENWA_DIR/reminders/"
[[ -f "$OPENWA_DIR/reminders/contacts.txt" ]] || cp "$ROOT/reminders/contacts.example.txt" "$OPENWA_DIR/reminders/contacts.txt"
[[ -f "$OPENWA_DIR/reminders/message.txt"  ]] || cp "$ROOT/reminders/message.example.txt"  "$OPENWA_DIR/reminders/message.txt"
chmod 750 "$OPENWA_DIR/reminders/send-reminder.sh"

# --- AI bot -------------------------------------------------------------------
mkdir -p "$OPENWA_DIR/ai-bot"
cp "$ROOT/ai-bot/bot.py" "$OPENWA_DIR/ai-bot/"
chmod 755 "$OPENWA_DIR/ai-bot/bot.py"
[[ -f "$OPENWA_DIR/ai-bot/persona.txt" ]] || cp "$ROOT/ai-bot/persona.example.txt" "$OPENWA_DIR/ai-bot/persona.txt"
if [[ ! -f "$OPENWA_DIR/ai-bot/ai-bot.env" ]]; then
  sed "s|__HOME__|$HOME|g" "$ROOT/ai-bot/ai-bot.env.example" > "$OPENWA_DIR/ai-bot/ai-bot.env"
  chmod 600 "$OPENWA_DIR/ai-bot/ai-bot.env"
fi

# --- systemd ------------------------------------------------------------------
mkdir -p "$HOME/.config/systemd/user"
sed "s|__HOME__|$HOME|g" "$ROOT/systemd/openwa-ai-bot.service" > "$HOME/.config/systemd/user/openwa-ai-bot.service"
systemctl --user daemon-reload
systemctl --user enable --now openwa-ai-bot 2>/dev/null || systemctl --user restart openwa-ai-bot

echo
echo "✅ kit installed into $OPENWA_DIR (bot service: openwa-ai-bot)"
echo
echo "Manual steps that must never be automated:"
echo "  1. if not yet linked: dashboard http://localhost:2785 → scan the QR"
echo "     with a DEDICATED number (unofficial automation = ban risk)"
echo "  2. copy the admin key out of the container (root-owned):"
echo "     sudo sh -c 'cat $OPENWA_DIR/data/.api-key > $OPENWA_DIR/.api-key && chown \$USER:$USER $OPENWA_DIR/.api-key && chmod 400 $OPENWA_DIR/.api-key'"
echo "  3. create the OPERATOR key for the AI bot (POST /api/auth/api-keys,"
echo '     role operator, allowedSessions=[your session id]) and save it as:'
echo "     $OPENWA_DIR/.ai-agent-key        (chmod 400)"
echo "  4. paste the session UUID into:  $OPENWA_DIR/reminders/session-id"
echo "  5. put your Gemini key + real home paths into:  $OPENWA_DIR/ai-bot/ai-bot.env"
echo "  6. systemctl --user restart openwa-ai-bot"
