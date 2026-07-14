#!/bin/zsh
# AI Pulse daily curation run. Invoked manually or by launchd.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p logs
exec .venv/bin/python curator.py >> "logs/curator-$(date +%Y-%m-%d).log" 2>&1
