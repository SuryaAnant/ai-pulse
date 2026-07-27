#!/bin/zsh
# AI Pulse daily curation run. Invoked manually or by launchd.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p logs
LOG="logs/curator-$(date +%Y-%m-%d).log"

# Idempotency guard (28 Jul 2026): the agent now also fires at login via RunAtLoad,
# so this can be invoked several times a day. If today's briefing already exists,
# do nothing — no duplicate API spend, no pointless commit.
# NOTE: curator.py names its output by UTC date (datetime.now(timezone.utc)),
# so the guard must use UTC too. Using local time silently fails between
# 00:00 and 02:00 CEST, when UTC is still the previous day — which is exactly
# when Surya is often awake. Found the hard way on 28 Jul 2026.
TODAY_JSON="data/$(date -u +%Y-%m-%d).json"
if [ -s "$TODAY_JSON" ]; then
  echo "$(date): briefing for $(date +%F) already exists, skipping" >> "$LOG"
  exit 0
fi
.venv/bin/python curator.py >> "$LOG" 2>&1
rc=$?

# Publish the fresh briefing to GitHub Pages (skipped until a remote is set).
if [ $rc -eq 0 ] && git remote get-url origin > /dev/null 2>&1; then
  git add data/ >> "$LOG" 2>&1
  git diff --cached --quiet || git commit -m "Daily briefing $(date +%F)" >> "$LOG" 2>&1
  git pull --rebase origin main >> "$LOG" 2>&1
  git push origin main >> "$LOG" 2>&1
fi

exit $rc
