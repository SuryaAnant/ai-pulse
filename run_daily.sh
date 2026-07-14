#!/bin/zsh
# AI Pulse daily curation run. Invoked manually or by launchd.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p logs
LOG="logs/curator-$(date +%Y-%m-%d).log"
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
