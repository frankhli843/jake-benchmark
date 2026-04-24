#!/usr/bin/env python3
"""Scan r/LocalLLaMA for new models/quants/configs worth benchmarking.

Uses Reddit's RSS/JSON feeds (no auth required).
Deduplicates against a state file to avoid reprocessing.
Outputs JSON array of candidates to stdout.

Usage:
    python3 scan-localllama.py [--state-file PATH] [--max-posts 50] [--dry-run]

State file: skills/jake-benchmark/state/localllama-seen.json
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE_FILE = os.path.join(SCRIPT_DIR, "..", "state", "localllama-seen.json")

# Reddit JSON endpoint (no auth needed, rate-limited to ~60 req/min)
REDDIT_URL = "https://www.reddit.com/r/LocalLLaMA/new.json?limit={limit}"

# Keywords that indicate a post about a new model release or benchmark-worthy quant
MODEL_KEYWORDS = [
    r"\breleas(?:e|ed|ing)\b",
    r"\blaunch(?:ed|ing)?\b",
    r"\bnew model\b",
    r"\bquant(?:s|ized|ization)?\b",
    r"\bgguf\b",
    r"\bollama\b",
    r"\bfinetun(?:e|ed|ing)\b",
    r"\bbenchmark(?:s|ed|ing)?\b",
    r"\b\d+[bB]\b",  # parameter counts like 7B, 27b, 70B
    r"\bq[248]_[kK]_[mMsS]\b",  # quant names like q4_K_M
    r"\bfp16\b",
    r"\bawq\b",
    r"\bgptq\b",
    r"\bexl2\b",
    r"\bmlx\b",
]

# Model name patterns (catches things like "qwen3:8b", "gemma-4-27b", "llama-4-scout")
MODEL_NAME_PATTERNS = [
    r"(?:qwen|gemma|llama|mistral|deepseek|phi|codestral|nemotron|command|glm|yi|internlm|starcoder|falcon|mamba|jamba|granite|olmo|aya|dbrx|cohere|arctic|smol|lfm|exaone|solar)\s*[-_]?\s*\d",
    r"\b\w+[-_]?\d+[bB](?:[-_]\w+)?\b",  # generic "<name>-<N>b" patterns
]

# Flair patterns that indicate model releases
RELEASE_FLAIRS = [
    "new model", "news", "resources", "discussion", "generation",
]

# Minimum relevance score to be considered a candidate
MIN_RELEVANCE = 2

# Size constraints for Jake benchmark (RTX 3090 24GB VRAM)
# Models above ~35B dense or ~70B MoE are too large
MAX_PARAMS_DENSE = 35  # billions
MAX_PARAMS_MOE = 70


def fetch_reddit_posts(limit=50, retries=3):
    """Fetch recent posts from r/LocalLLaMA using JSON API with retry."""
    url = REDDIT_URL.format(limit=limit)
    headers = {
        "User-Agent": "jake-benchmark-scanner/1.0 (benchmark model discovery)",
    }
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                posts = []
                for child in data.get("data", {}).get("children", []):
                    d = child.get("data", {})
                    posts.append({
                        "id": d.get("id", ""),
                        "title": d.get("title", ""),
                        "selftext": (d.get("selftext") or "")[:2000],
                        "url": d.get("url", ""),
                        "permalink": f"https://reddit.com{d.get('permalink', '')}",
                        "score": d.get("score", 0),
                        "num_comments": d.get("num_comments", 0),
                        "created_utc": d.get("created_utc", 0),
                        "link_flair_text": (d.get("link_flair_text") or "").lower(),
                        "author": d.get("author", ""),
                    })
                return posts
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 10 * (attempt + 1)
                print(f"Rate limited (429), retrying in {wait}s... (attempt {attempt + 1}/{retries})", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"Error fetching Reddit: {e}", file=sys.stderr)
            return []
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"Fetch error: {e}, retrying in {wait}s... (attempt {attempt + 1}/{retries})", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"Error fetching Reddit: {e}", file=sys.stderr)
            return []
    return []


def extract_model_names(text):
    """Extract potential model names from text."""
    models = set()
    for pattern in MODEL_NAME_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            models.add(m.group(0).strip().lower())
    return list(models)


def extract_param_count(text):
    """Extract parameter count in billions from text. Returns None if not found."""
    # Look for patterns like "27B", "7b", "70B"
    m = re.search(r"(\d+(?:\.\d+)?)\s*[bB]\b", text)
    if m:
        return float(m.group(1))
    return None


def extract_quant_info(text):
    """Extract quantization info from text."""
    quants = set()
    for pattern in [r"[qQ][2-8]_[kK]_[mMsS]", r"\bGGUF\b", r"\bAWQ\b", r"\bGPTQ\b", r"\bEXL2\b", r"\bMLX\b", r"\bfp16\b", r"\bfp8\b"]:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            quants.add(m.group(0).upper())
    return list(quants)


def score_relevance(post):
    """Score how relevant a post is for benchmarking. Higher = more relevant."""
    score = 0
    text = f"{post['title']} {post['selftext']}".lower()

    # Keyword matches
    for kw in MODEL_KEYWORDS:
        if re.search(kw, text, re.IGNORECASE):
            score += 1

    # Model name matches
    model_names = extract_model_names(text)
    if model_names:
        score += 2

    # Quant info present
    quants = extract_quant_info(text)
    if quants:
        score += 2

    # Flair bonus
    if post["link_flair_text"] in RELEASE_FLAIRS:
        score += 1

    # Engagement bonus (popular posts about models are more likely to be significant)
    if post["score"] > 100:
        score += 1
    if post["score"] > 500:
        score += 1

    # Param count present
    params = extract_param_count(text)
    if params:
        score += 1
        # Size penalty: too large for RTX 3090
        if params > MAX_PARAMS_DENSE:
            score -= 2

    return score


def load_state(state_file):
    """Load the seen-posts state file."""
    if os.path.exists(state_file):
        with open(state_file) as f:
            return json.load(f)
    return {"seen_ids": {}, "last_scan": None, "candidates_history": []}


def save_state(state_file, state):
    """Save state to file."""
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def already_benchmarked(model_names, runs_dir=None):
    """Check if any of these model names have already been benchmarked."""
    if runs_dir is None:
        runs_dir = os.path.join(SCRIPT_DIR, "..", "runs")
    if not os.path.isdir(runs_dir):
        return set()

    benchmarked = set()
    existing_runs = os.listdir(runs_dir)
    for model in model_names:
        model_lower = model.lower().replace(" ", "").replace("-", "").replace("_", "")
        for run_name in existing_runs:
            run_lower = run_name.lower().replace(" ", "").replace("-", "").replace("_", "")
            if model_lower in run_lower:
                benchmarked.add(model)
                break
    return benchmarked


def main():
    parser = argparse.ArgumentParser(description="Scan r/LocalLLaMA for new models to benchmark")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="Path to dedup state file")
    parser.add_argument("--max-posts", type=int, default=50, help="Max posts to fetch")
    parser.add_argument("--dry-run", action="store_true", help="Don't update state file")
    parser.add_argument("--min-score", type=int, default=MIN_RELEVANCE, help="Minimum relevance score")
    parser.add_argument("--json", action="store_true", help="Output raw JSON (default)")
    parser.add_argument("--summary", action="store_true", help="Output human-readable summary")
    args = parser.parse_args()

    state = load_state(args.state_file)
    posts = fetch_reddit_posts(limit=args.max_posts)

    if not posts:
        if args.summary:
            print("No posts fetched from r/LocalLLaMA (rate limited or network error)")
        else:
            json.dump({"candidates": [], "error": "fetch_failed"}, sys.stdout, indent=2)
        return

    candidates = []
    new_seen = {}

    for post in posts:
        pid = post["id"]

        # Skip already-seen posts
        if pid in state["seen_ids"]:
            continue

        relevance = score_relevance(post)
        model_names = extract_model_names(f"{post['title']} {post['selftext']}")
        quants = extract_quant_info(f"{post['title']} {post['selftext']}")
        params = extract_param_count(f"{post['title']} {post['selftext']}")

        new_seen[pid] = {
            "title": post["title"][:200],
            "relevance": relevance,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

        if relevance >= args.min_score:
            # Check if already benchmarked
            already = already_benchmarked(model_names)

            candidate = {
                "post_id": pid,
                "title": post["title"],
                "url": post["permalink"],
                "relevance_score": relevance,
                "reddit_score": post["score"],
                "comments": post["num_comments"],
                "model_names": model_names,
                "quant_types": quants,
                "param_count_b": params,
                "already_benchmarked": list(already),
                "new_models": [m for m in model_names if m not in already],
                "posted_utc": datetime.fromtimestamp(post["created_utc"], tz=timezone.utc).isoformat(),
                "flair": post["link_flair_text"],
            }
            candidates.append(candidate)

    # Sort by relevance score descending
    candidates.sort(key=lambda c: c["relevance_score"], reverse=True)

    # Update state
    if not args.dry_run:
        state["seen_ids"].update(new_seen)
        state["last_scan"] = datetime.now(timezone.utc).isoformat()
        # Trim seen_ids to last 2000 entries to prevent unbounded growth
        if len(state["seen_ids"]) > 2000:
            sorted_ids = sorted(
                state["seen_ids"].items(),
                key=lambda x: x[1].get("scanned_at", ""),
                reverse=True
            )
            state["seen_ids"] = dict(sorted_ids[:1500])
        # Record this scan's candidates (keep last 30 days)
        state["candidates_history"].append({
            "scan_date": datetime.now(timezone.utc).isoformat(),
            "count": len(candidates),
            "top_titles": [c["title"][:100] for c in candidates[:5]],
        })
        state["candidates_history"] = state["candidates_history"][-30:]
        save_state(args.state_file, state)

    if args.summary:
        print(f"Scanned {len(posts)} posts, {len(new_seen)} new, {len(candidates)} candidates")
        if candidates:
            print("\nTop candidates:")
            for c in candidates[:10]:
                new_flag = " [NEW]" if c["new_models"] else " [already tested]"
                models = ", ".join(c["model_names"][:3]) or "?"
                quants = ", ".join(c["quant_types"][:3]) or "none"
                print(f"  [{c['relevance_score']}] {c['title'][:80]}")
                print(f"      Models: {models} | Quants: {quants} | Params: {c['param_count_b'] or '?'}B{new_flag}")
                print(f"      {c['url']}")
        else:
            print("No new benchmark-worthy posts found.")
    else:
        result = {
            "scan_date": datetime.now(timezone.utc).isoformat(),
            "posts_fetched": len(posts),
            "new_posts": len(new_seen),
            "candidates": candidates,
        }
        json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
