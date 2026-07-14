#!/usr/bin/env python3
"""AI Pulse — daily AI news curator.

Fetches curated RSS sources, dedupes and ranks stories, then uses the
`claude` CLI (headless mode, runs on your Claude Code login) to write a
brief summary, impact analysis, and suggested action for each story.

Output: data/YYYY-MM-DD.json and data/latest.json, consumed by index.html.
"""

import concurrent.futures
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MAX_AGE_HOURS = 36
MAX_STORIES = 25
FETCH_TIMEOUT = 20
CLAUDE_MODEL = "sonnet"  # change to "opus" for higher quality, "haiku" for speed
USER_AGENT = "AIPulse/1.0 (personal news curator; +https://github.com)"

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
OG_IMG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image(?::url)?["\']',
    re.I,
)


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def clean_text(raw: str, limit: int = 600) -> str:
    text = html.unescape(TAG_RE.sub(" ", raw or ""))
    text = WS_RE.sub(" ", text).strip()
    return text[:limit]


def fetch_feed(source: dict) -> list[dict]:
    req = urllib.request.Request(source["url"], headers={"User-Agent": USER_AGENT})
    body = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                body = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:  # Reddit throttles bursts
                time.sleep(5 * (attempt + 1))
                continue
            raise
    parsed = feedparser.parse(body)
    items = []
    for e in parsed.entries[:30]:
        image = ""
        for media in (e.get("media_content") or []) + (e.get("media_thumbnail") or []):
            if media.get("url"):
                image = media["url"]
                break
        if not image:
            for link in e.get("links", []):
                if link.get("rel") == "enclosure" and "image" in link.get("type", ""):
                    image = link.get("href", "")
                    break
        if not image:
            m = IMG_RE.search(e.get("summary", "") or "")
            if m:
                image = m.group(1)
        ts = e.get("published_parsed") or e.get("updated_parsed")
        published = (
            datetime.fromtimestamp(time.mktime(ts), tz=timezone.utc) if ts else None
        )
        items.append(
            {
                "title": clean_text(e.get("title", ""), 300),
                "link": e.get("link", ""),
                "snippet": clean_text(e.get("summary", "") or e.get("description", "")),
                "published": published.isoformat() if published else None,
                "image": image,
                "source": source["name"],
                "category": source["category"],
                "bucket": source.get("bucket", "ai"),
                "weight": source.get("weight", 0.8),
                "keyword_filter": source.get("keyword_filter", False),
            }
        )
    return items


def fetch_all(sources: list[dict]) -> tuple[list[dict], list[str]]:
    items, failed = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_feed, s): s for s in sources}
        for fut in concurrent.futures.as_completed(futures):
            src = futures[fut]
            try:
                got = fut.result()
                items.extend(got)
                log(f"  {src['name']}: {len(got)} items")
            except Exception as exc:
                failed.append(src["name"])
                log(f"  {src['name']}: FAILED ({exc})")
    return items, failed


def fetch_og_image(url: str) -> str:
    """Pull og:image from the article page for stories whose feed had no image."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            head = resp.read(120_000).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    m = OG_IMG_RE.search(head)
    if not m:
        return ""
    img = m.group(1) or m.group(2) or ""
    return img if img.startswith("http") else ""


def fill_missing_images(stories: list[dict]) -> None:
    missing = [s for s in stories if not s.get("image")]
    if not missing:
        return
    log(f"Fetching og:image for {len(missing)} stories without a feed image...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_og_image, s["link"]): s for s in missing}
        for fut in concurrent.futures.as_completed(futures):
            futures[fut]["image"] = fut.result()


def is_ai_related(item: dict, keywords: list[str]) -> bool:
    text = f"{item['title']} {item['snippet']}".lower()
    # Word-boundary match so "ai" doesn't hit "certain" and "compute" doesn't
    # hit "computer"; optional suffix keeps plurals/gerunds matching.
    return any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(kw)}(?:s|es|e|ing|ed)?(?![a-z0-9])", text
        )
        for kw in keywords
    )


def title_tokens(title: str) -> set[str]:
    stop = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "with", "its", "is"}
    return {t for t in re.findall(r"[a-z0-9]+", title.lower()) if t not in stop}


def dedupe(items: list[dict]) -> list[dict]:
    """Merge near-duplicate stories; count of merged sources boosts ranking."""
    kept: list[dict] = []
    for item in sorted(items, key=lambda x: -x["weight"]):
        toks = title_tokens(item["title"])
        if not toks:
            continue
        matched = None
        for k in kept:
            ktoks = k["_tokens"]
            overlap = len(toks & ktoks) / max(1, len(toks | ktoks))
            if overlap >= 0.5:
                matched = k
                break
        if matched:
            matched["source_count"] += 1
            matched["other_sources"].append(item["source"])
        else:
            item = dict(item)
            item["_tokens"] = toks
            item["source_count"] = 1
            item["other_sources"] = []
            kept.append(item)
    return kept


def score(item: dict, now: datetime) -> float:
    s = item["weight"] + 0.6 * (item["source_count"] - 1)
    if item["published"]:
        age_h = (now - datetime.fromisoformat(item["published"])).total_seconds() / 3600
        s += max(0.0, 0.5 * (1 - age_h / MAX_AGE_HOURS))
    return s


def build_prompt(stories: list[dict]) -> str:
    payload = [
        {
            "id": i,
            "title": s["title"],
            "source": s["source"],
            "also_covered_by": s["other_sources"],
            "snippet": s["snippet"],
        }
        for i, s in enumerate(stories)
    ]
    prompt = f"""You are the editor of a daily briefing for a builder who is three people at once: an AI engineer building agents, an SRE/DevOps engineer at a big tech company, and a future startup founder. The briefing is mostly AI news, with a small SRE/DevOps and general-tech section — the signal without reading 30 sources.

Write in the Smart Brevity style (Axios) with Inshorts' facts-only discipline.

Below is a JSON array of today's top AI stories (title, source, snippet).
For EACH story, write:
- "title": a tease headline, max 10 words. Active voice, present tense, strongest verb you can justify. Include the key number when there is one ("SambaNova raises $1B", not "SambaNova raises funding"). Never withhold the point for a click ("You won't believe..." is banned).
- "summary": under 60 words. The FIRST sentence must state the news outcome plainly — who did what, with the key number — as if it's the only sentence the reader will see. Remaining 1-2 sentences add the most essential context. No throat-clearing ("In a recent development..."), no opinions, no hype adjectives.
- "key_fact": the single most striking number or concrete fact in the story, as a short fragment (e.g. "$11B valuation", "2.7T parameters", "194 points on HN"). Empty string if the story has no standout fact.
- "impact": 1-2 sentences of "why this matters" — concrete for developers, founders, or investors. Name who is affected and how, not vague significance.
- "action": one concrete, practical suggestion for the reader (e.g. "try X", "watch for Y", "if you build Z, consider..."). Under 25 words.
- "topic": exactly one of: "models", "startups-funding", "products", "research", "policy", "infrastructure", "open-source", "sre-devops" (use "sre-devops" for reliability/DevOps/platform-engineering stories)
- "importance": integer 1-5 (5 = industry-shifting, 1 = minor)

FACTS-ONLY RULE: base everything strictly on the given title/snippet — never invent specifics (numbers, quotes, names) that are not supported by them. If a snippet is thin, write a shorter summary rather than speculating. It is better to omit key_fact than to guess one.

Respond with ONLY a JSON array (no markdown fences, no commentary), one object per input story, each including the original "id".

INPUT STORIES:
{json.dumps(payload, ensure_ascii=False, indent=1)}"""
    return prompt


def parse_json_array(text: str) -> list[dict] | None:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        log(f"No JSON array in model output: {text[:200]}")
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        log(f"Failed to parse model JSON: {exc}")
        return None


def summarize_via_cli(prompt: str) -> list[dict] | None:
    """Headless `claude -p` — runs on the local Claude Code login."""
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", CLAUDE_MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log(f"claude call failed: {exc}")
        return None
    if result.returncode != 0:
        log(f"claude exited {result.returncode}: {result.stderr[:300]}")
        return None
    return parse_json_array(result.stdout.strip())


def summarize_via_sdk(prompt: str) -> list[dict] | None:
    """Anthropic SDK — used in cloud runs where ANTHROPIC_API_KEY is set."""
    try:
        import anthropic
    except ImportError:
        log("anthropic package not installed; cannot use SDK path")
        return None
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=16000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.RateLimitError:
        log("SDK: rate limited")
        return None
    except anthropic.APIStatusError as exc:
        log(f"SDK: API error {exc.status_code}: {exc.message}")
        return None
    except anthropic.APIConnectionError:
        log("SDK: network error")
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    return parse_json_array(text)


def summarize_with_claude(stories: list[dict]) -> list[dict] | None:
    """One model call that writes summary/impact/action for all stories."""
    prompt = build_prompt(stories)
    if os.environ.get("ANTHROPIC_API_KEY"):
        log(f"Summarizing {len(stories)} stories via Anthropic SDK (opus 4.8)...")
        return summarize_via_sdk(prompt)
    log(f"Summarizing {len(stories)} stories via claude CLI ({CLAUDE_MODEL})...")
    return summarize_via_cli(prompt)


def main() -> int:
    config = json.loads((ROOT / "sources.json").read_text())
    sources, keywords = config["sources"], config["ai_keywords"]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)

    log(f"Fetching {len(sources)} sources...")
    items, failed = fetch_all(sources)
    log(f"Fetched {len(items)} items total ({len(failed)} sources failed)")

    fresh = []
    for it in items:
        if it["published"] and datetime.fromisoformat(it["published"]) < cutoff:
            continue
        if it["keyword_filter"] and not is_ai_related(it, keywords):
            continue
        if not it["title"] or not it["link"]:
            continue
        fresh.append(it)
    log(f"{len(fresh)} items within last {MAX_AGE_HOURS}h")

    unique = dedupe(fresh)
    unique.sort(key=lambda x: -score(x, now))
    # Per-bucket caps keep the briefing a ~10-minute read: the AI bucket is
    # the main course, SRE/DevOps and general tech stay small side dishes.
    caps = dict(config.get("bucket_caps", {"ai": MAX_STORIES}))
    taken: dict[str, int] = {}
    top = []
    for it in unique:
        b = it["bucket"]
        if taken.get(b, 0) >= caps.get(b, 0):
            continue
        taken[b] = taken.get(b, 0) + 1
        top.append(it)
    log(f"{len(unique)} unique stories, taking {len(top)} ({taken})")

    fill_missing_images(top)
    with_img = sum(1 for s in top if s.get("image"))
    log(f"{with_img}/{len(top)} stories have an image")

    enriched = summarize_with_claude(top)
    articles = []
    by_id = {e["id"]: e for e in enriched} if enriched else {}
    for i, s in enumerate(top):
        e = by_id.get(i, {})
        articles.append(
            {
                "title": e.get("title") or s["title"],
                "summary": e.get("summary") or s["snippet"][:350],
                "key_fact": e.get("key_fact", ""),
                "image": s.get("image", ""),
                "impact": e.get("impact", ""),
                "action": e.get("action", ""),
                "topic": e.get("topic", "products"),
                "importance": e.get("importance", 2),
                "link": s["link"],
                "source": s["source"],
                "also_covered_by": s["other_sources"],
                "published": s["published"],
            }
        )
    articles.sort(key=lambda a: (-a["importance"], a["source"]))

    output = {
        "generated_at": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "ai_enriched": enriched is not None,
        "sources_failed": failed,
        "articles": articles,
    }
    DATA_DIR.mkdir(exist_ok=True)
    day_file = DATA_DIR / f"{output['date']}.json"
    day_file.write_text(json.dumps(output, ensure_ascii=False, indent=1))
    (DATA_DIR / "latest.json").write_text(json.dumps(output, ensure_ascii=False, indent=1))
    dates = sorted(p.stem for p in DATA_DIR.glob("????-??-??.json"))
    (DATA_DIR / "index.json").write_text(json.dumps({"dates": dates}))
    log(f"Wrote {len(articles)} articles -> {day_file.name}, latest.json, index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
