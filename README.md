# AI Pulse

Personal daily AI-news briefing. Pulls the sources the best-informed people follow
(TechCrunch AI, VentureBeat, The Verge, Ars Technica, THE DECODER, MIT Tech Review,
OpenAI / DeepMind / Google / Hugging Face blogs, Hacker News 100+, r/LocalLLaMA,
r/MachineLearning, r/ai_agents, Simon Willison, Interconnects, Import AI),
dedupes and ranks the stories, then uses Claude to write for each one:

- a crisp headline
- a summary under 60 words
- **Why it matters** (impact)
- **What to do** (one concrete action)

## Run it

```sh
./run_daily.sh          # fetch + curate + summarize -> data/latest.json
python3 -m http.server 8787   # then open http://localhost:8787
```

The AI summarization runs through `claude -p` (headless Claude Code) on your
existing Claude login — no API key needed. Change the model in `curator.py`
(`CLAUDE_MODEL = "sonnet"`).

## Daily schedule

A launchd agent (`~/Library/LaunchAgents/com.surya.aipulse.plist`) runs the
curator every day at 07:30. Manage it with:

```sh
launchctl unload ~/Library/LaunchAgents/com.surya.aipulse.plist   # disable
launchctl load ~/Library/LaunchAgents/com.surya.aipulse.plist     # enable
```

Logs land in `logs/`.

## Files

| File | Purpose |
|---|---|
| `sources.json` | The source list + AI keyword filter. Add/remove feeds here. |
| `curator.py` | Fetch → filter (36h) → dedupe → rank → Claude enrichment → JSON |
| `index.html` | Static frontend, reads `data/latest.json` |
| `data/YYYY-MM-DD.json` | Daily archive |
| `run_daily.sh` | Cron entrypoint |

## Publishing

The output is a static site (`index.html` + `data/`), and everything needed to
run it in the cloud is already in place:

- `curator.py` automatically switches from `claude -p` to the Anthropic Python
  SDK (Claude Opus 4.8) when `ANTHROPIC_API_KEY` is set in the environment.
- `.github/workflows/curate.yml` runs the curator daily at 07:30 IST on GitHub
  Actions and commits the fresh `data/` files.

To go live:

1. Create a GitHub repo and push this folder.
2. Add an `ANTHROPIC_API_KEY` repository secret (Settings → Secrets → Actions).
3. Enable GitHub Pages (Settings → Pages → deploy from `main`, root).

Your briefing is then public at `https://<user>.github.io/<repo>/` and
refreshes itself every morning.
