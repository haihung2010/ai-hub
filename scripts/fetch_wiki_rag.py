#!/usr/bin/env python3
"""
Fetch recent Wikipedia articles and ingest into AI Hub RAG.
Targets: recent events, tech updates, world news (2025-2026).
Usage:
    python scripts/fetch_wiki_rag.py [--project PROJECT] [--domain DOMAIN] [--limit N]
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

API_BASE = "http://localhost:8000"
API_KEY = None  # Set via --api-key or auto-detect from .env

def get_api_key():
    global API_KEY
    if API_KEY:
        return API_KEY
    try:
        with open("/home/hung/ai-hub/.env") as f:
            for line in f:
                if line.startswith("API_KEY="):
                    API_KEY = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                    return API_KEY
    except Exception:
        pass
    print("ERROR: Cannot find API_KEY. Use --api-key flag.")
    sys.exit(1)

def api_post(endpoint, data):
    url = f"{API_BASE}{endpoint}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-API-KEY", get_api_key())
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        print(f"  ERROR {e.code}: {err_body[:200]}")
        return None

def wiki_search(query, limit=5):
    """Search Wikipedia for articles."""
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json",
        "srprop": "snippet",
    })
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "AIHub-RAG-Bot/1.0")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data.get("query", {}).get("search", [])
    except Exception as e:
        print(f"  Wiki search error: {e}")
        return []

def wiki_get_content(title):
    """Get full Wikipedia article content."""
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query",
        "titles": title,
        "prop": "extracts",
        "explaintext": "true",
        "exsectionformat": "plain",
        "format": "json",
    })
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "AIHub-RAG-Bot/1.0")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            pages = data.get("query", {}).get("pages", {})
            for page_id, page in pages.items():
                if page_id != "-1":
                    return page.get("extract", "")
    except Exception as e:
        print(f"  Wiki content error: {e}")
    return ""

def wiki_get_recent_changes(days=30, limit=20):
    """Get recently changed Wikipedia articles (hot topics)."""
    from datetime import datetime, timedelta
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query",
        "list": "recentchanges",
        "rcstart": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rcend": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rcnamespace": "0",
        "rclimit": limit,
        "rcprop": "title|sizes|timestamp",
        "rctype": "edit|new",
        "format": "json",
    })
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "AIHub-RAG-Bot/1.0")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data.get("query", {}).get("recentchanges", [])
    except Exception as e:
        print(f"  Wiki recent changes error: {e}")
        return []

# === Topic lists for RAG enrichment ===

TECH_TOPICS = [
    "Gemma 4 language model",
    "Large language model 2025",
    "Retrieval-augmented generation",
    "Multimodal large language model",
    "AI agent framework 2025",
    "OpenAI GPT-5",
    "Claude 4 language model",
    "DeepSeek V3",
    "Llama 4 model",
    "Qwen language model",
    "Mixtral mixture of experts",
    "Vector database",
    "Ollama software",
    "llama.cpp",
    "GGUF format",
    "Model quantization",
    "LoRA fine-tuning",
    "RLHF reinforcement learning",
    "Chain of thought prompting",
    "Function calling LLM",
]

WORLD_TOPICS_2025 = [
    "2025 in technology",
    "2025 in artificial intelligence",
    "2025 global economy",
    "2025 Vietnam",
    "ASEAN 2025",
    "Semiconductor industry 2025",
    "Electric vehicle 2025",
    "Space exploration 2025",
    "Climate change 2025",
    "Cryptocurrency 2025",
    "Quantum computing 2025",
    "5G deployment",
    "Autonomous driving 2025",
    "Robotics 2025",
    "Biotechnology 2025",
]

VIETNAM_TOPICS = [
    "Vietnam economy 2025",
    "Vietnam technology",
    "Ho Chi Minh City",
    "Vietnam stock market",
    "Vietnam digital transformation",
    "Vingroup",
    "FPT Corporation",
    "Vietnam semiconductor",
    "Vietnam AI development",
]

FINANCE_TOPICS = [
    "S&P 500 2025",
    "Bitcoin 2025",
    "Federal Reserve 2025",
    "Global inflation 2025",
    "Stock market 2025",
    "Venture capital AI",
    "Tech stocks 2025",
]

def ingest_card(title, content, project, domain):
    """Ingest a knowledge card into AI Hub RAG."""
    # Truncate if too long (API may have limits)
    if len(content) > 50000:
        content = content[:50000] + "\n\n[Content truncated]"
    return api_post("/v1/admin/knowledge/upload", {
        "project_id": project,
        "title": title,
        "content": content,
        "domain": domain,
    })

def run(topics, project, domain, limit_per_topic=3, dry_run=False):
    """Fetch and ingest Wikipedia articles for given topics."""
    total = 0
    errors = 0
    skipped = 0

    for topic in topics[:limit_per_topic * 5]:  # Safety cap
        if total >= limit_per_topic * len(topics):
            break

        print(f"\n🔍 Searching: {topic}")
        results = wiki_search(topic, limit=2)

        for r in results:
            title = r.get("title", "")
            if not title:
                continue

            print(f"  📄 Fetching: {title}")
            content = wiki_get_content(title)

            if not content or len(content) < 200:
                print(f"  ⏭️  Skipped (too short: {len(content or '')} chars)")
                skipped += 1
                continue

            # Add metadata header
            now = datetime.utcnow().strftime("%Y-%m-%d")
            header = f"[Source: Wikipedia | Fetched: {now} | Topic: {topic}]\n\n"
            full_content = header + content

            if dry_run:
                print(f"  ✅ DRY RUN: Would ingest ({len(full_content)} chars)")
                total += 1
            else:
                result = ingest_card(title, full_content, project, domain)
                if result:
                    print(f"  ✅ Ingested ({len(full_content)} chars)")
                    total += 1
                else:
                    print(f"  ❌ Failed to ingest")
                    errors += 1

            time.sleep(5)  # Rate limit Wikipedia API (strict)

    return total, errors, skipped

def main():
    parser = argparse.ArgumentParser(description="Fetch Wikipedia data into AI Hub RAG")
    parser.add_argument("--project", default="wiki_enrichment", help="Project ID for RAG cards")
    parser.add_argument("--domain", default="general", help="Knowledge domain")
    parser.add_argument("--api-key", help="API key (auto-detected from .env)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without ingesting")
    parser.add_argument("--limit", type=int, default=3, help="Articles per topic category")
    parser.add_argument("--categories", nargs="+",
                       default=["tech", "world", "vietnam", "finance"],
                       help="Categories to fetch: tech world vietnam finance")
    args = parser.parse_args()

    if args.api_key:
        global API_KEY
        API_KEY = args.api_key

    # Build topic list from categories
    topic_map = {
        "tech": TECH_TOPICS,
        "world": WORLD_TOPICS_2025,
        "vietnam": VIETNAM_TOPICS,
        "finance": FINANCE_TOPICS,
    }

    all_topics = []
    for cat in args.categories:
        if cat in topic_map:
            all_topics.extend(topic_map[cat])
        else:
            print(f"Unknown category: {cat}")

    if not all_topics:
        print("No topics selected. Use --categories tech world vietnam finance")
        sys.exit(1)

    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  AI Hub — Wikipedia RAG Enrichment")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Project: {args.project}")
    print(f"  Domain: {args.domain}")
    print(f"  Categories: {', '.join(args.categories)}")
    print(f"  Topics: {len(all_topics)}")
    print(f"  Limit/topic: {args.limit}")
    print(f"  Dry run: {args.dry_run}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    total, errors, skipped = run(all_topics, args.project, args.domain, args.limit, args.dry_run)

    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  DONE: {total} ingested, {errors} errors, {skipped} skipped")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    main()
