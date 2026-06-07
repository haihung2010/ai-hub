#!/usr/bin/env python3
"""RAG knowledge scraper + ingest pipeline.

Uses MiniMax WebSearch MCP to discover authoritative sources, fetches the
top results via httpx, dedupes by URL + content hash, and writes them to
PostgreSQL via the existing ``KnowledgeIngestionService`` (which handles
chunking + embedding for free).

Domain selection rationale (see reports/rag-scrape-2026-06-06.md §0):

* ``ihi-standards``: ISO 10816-3, NEMA MG-1, IEEE 1159, IEC 61000. Standards
  docs are deterministic, well-structured, high-trust, and align with the
  in-flight IHI work (without touching the separate ``ihi_rag_cases`` table).
* ``vi-fanpage``: Vietnamese customer-service Q&A. Broad, real-world,
  exercises the multilingual path of the embedding model.

Usage:
    python scripts/scrape_rag.py \\
        --project default --tenant default \\
        --domains ihi-standards vi-fanpage \\
        --max-per-domain 15
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

# Make app importable when run from any cwd
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Set DB url early so settings can be loaded
os.environ.setdefault("DATABASE_URL", "postgresql://aihub:aihub_pass@localhost:5432/ai_hub")

from app.core.config import get_settings  # noqa: E402
from app.models.knowledge import KnowledgeCardCreate  # noqa: E402
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService  # noqa: E402
from app.services.knowledge_ingestion_service import KnowledgeIngestionService  # noqa: E402
from app.services.mcp.minimax_websearch import (  # noqa: E402
    MCPCircuitOpen,
    MCPError,
    MiniMaxMCPClient,
)

logger = logging.getLogger("scrape_rag")

# Per-domain search query plans. Each plan is a list of (query, weight) pairs.
# Weight hints at source quality: official standards > industry explainers >
# forum/QA content. We bias scraping toward higher-weight queries first.
DOMAIN_PLANS: dict[str, dict] = {
    "ihi-standards": {
        "label": "IHI industrial standards",
        "queries": [
            ("ISO 10816-3 mechanical vibration evaluation industrial machines", 5),
            ("NEMA MG-1 motor vibration limits", 5),
            ("IEEE 1159 power quality recommended practice", 5),
            ("IEC 61000 electromagnetic compatibility harmonics", 4),
            ("ISO 10816-3 zone boundaries evaluation criteria", 5),
            ("NEMA MG-1 Part 7 motor vibration limits table", 4),
            ("IEEE 1159 power quality disturbance categories transients", 4),
            ("IEC 61000-2-2 compatibility levels low frequency conducted", 4),
            ("ISO 10816-21 non-rotating parts measurement", 3),
            ("NEMA MG-1 Part 30 small motor vibration", 3),
            ("predictive maintenance vibration monitoring standard", 3),
            ("bearing fault frequency SKF ISO standards", 3),
            ("rotating machinery condition monitoring standards overview", 3),
            ("NEMA MG-1 Part 6 mechanical vibration", 3),
            ("ISO 20816-1 measurement and evaluation of machine vibration", 4),
        ],
        "trust_level": 5,  # standards
        "source_type": "web_scrape_standard",
    },
    "vi-fanpage": {
        "label": "Vietnamese fanpage Q&A",
        "queries": [
            ("chính sách đổi trả hàng online Việt Nam", 3),
            ("hướng dẫn đặt hàng qua Messenger Facebook", 3),
            ("giá sản phẩm khuyến mãi shopee Lazada", 3),
            ("phí vận chuyển giao hàng tiết kiệm", 3),
            ("thanh toán COD chuyển khoản ngân hàng", 3),
            ("địa chỉ cửa hàng chi nhánh Hà Nội", 3),
            ("bảo hành sản phẩm điện tử", 3),
            ("đổi trả trong 7 ngày điều kiện", 3),
            ("hỗ trợ khách hàng hotline email", 3),
            ("mã giảm giá voucher freeship", 3),
            ("sản phẩm còn hàng hết hàng", 3),
            ("tư vấn chọn size quần áo", 3),
            ("đặt lịch hẹn dịch vụ spa", 3),
            ("giờ mở cửa quán cafe nhà hàng", 3),
            ("đăng ký thành viên tích điểm", 3),
        ],
        "trust_level": 2,  # forum / general web
        "source_type": "web_scrape_vi",
    },
}

# Domains that should not be re-fetched (we just trust the search snippet).
SKIP_FETCH_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "instagram.com", "tiktok.com",
    "youtube.com", "google.com", "schema.org",
}

# Body length limits
_MAX_BODY_CHARS = 8000   # cap per-card content (KnowledgeIngestionService caps at 100k)
_MIN_BODY_CHARS = 200     # below this, just use snippet
_FETCH_TIMEOUT = 12.0
_USER_AGENT = "AIHub-RAG-Bot/1.0 (+https://htechlabsvn.com)"

# Rate-limit politeness
_SEARCH_DELAY_SECONDS = 1.0
_FETCH_DELAY_SECONDS = 0.4

# Postgres TEXT cannot contain 0x00 — strip them
_NUL_RE = re.compile(r"\x00")

# PDF / binary sniff: if the body has lots of NULs or non-printable, treat as binary
def _is_binary(s: str) -> bool:
    if not s:
        return False
    sample = s[:4096]
    nuls = sample.count("\x00")
    if nuls > 4:
        return True
    non_print = sum(1 for c in sample if ord(c) < 9 or (ord(c) > 13 and ord(c) < 32 and ord(c) != 27))
    return (non_print / max(1, len(sample))) > 0.10


def _clean_text(s: str) -> str:
    """Strip NUL bytes and other Postgres-incompatible chars."""
    if not s:
        return s
    s = _NUL_RE.sub("", s)
    # Replace other control chars that may break text search / display
    s = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", " ", s)
    return s


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ScrapeResult:
    url: str
    title: str
    snippet: str
    body: str
    domain: str
    query: str
    trust_level: int
    source_type: str
    fetch_ok: bool
    fetch_error: str = ""
    content_hash: str = ""


@dataclass
class ScrapeReport:
    domain: str
    queries_run: int = 0
    urls_discovered: int = 0
    urls_fetched: int = 0
    urls_deduped: int = 0
    cards_ingested: int = 0
    cards_failed: int = 0
    sample_titles: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_text(s: str) -> str:
    """NFKC-normalize, collapse whitespace, strip — for content hashing."""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _content_hash(title: str, body: str) -> str:
    return hashlib.sha256(_normalize_text(f"{title}::{body}").encode("utf-8")).hexdigest()[:32]


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _should_skip_fetch(url: str) -> bool:
    host = _host(url)
    return any(d in host for d in SKIP_FETCH_DOMAINS)


async def _fetch_body(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    """Fetch URL, return (title, plain-text body). Raises on failure."""
    resp = await client.get(
        url,
        timeout=_FETCH_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "vi,en;q=0.8"},
    )
    resp.raise_for_status()
    ctype = resp.headers.get("content-type", "").lower()
    if "html" not in ctype and "xml" not in ctype:
        # Treat anything non-HTML as binary (PDFs, images, etc.). Skip.
        raise ValueError(f"non-html content-type: {ctype[:40]}")
    text = resp.text
    if _is_binary(text):
        raise ValueError("response body looks binary")
    text = _clean_text(text)
    soup = BeautifulSoup(text, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.decompose()
    # Prefer <article> if present
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = root.get_text(separator="\n", strip=True) if root else ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text[: _MAX_BODY_CHARS]


def _to_card(scrape: ScrapeResult, *, project_id: str, tenant_id: str) -> KnowledgeCardCreate:
    """Build a KnowledgeCardCreate from a ScrapeResult."""
    # Pick best available content: fetched body > snippet
    body = scrape.body.strip() if scrape.body else ""
    body = _clean_text(body)
    if len(body) < _MIN_BODY_CHARS:
        body = _clean_text(scrape.snippet.strip())
    if not body:
        # last resort
        body = f"(empty content)\nURL: {scrape.url}\nSnippet: {scrape.snippet}"

    title = scrape.title.strip() or scrape.url
    summary = scrape.snippet[: 240].strip()
    if scrape.fetch_error and scrape.fetch_error != "":
        summary = f"[snippet-only; {scrape.fetch_error}] {summary}".strip()
    if len(summary) > 240:
        summary = summary[: 237] + "..."

    return KnowledgeCardCreate(
        project_id=project_id,
        tenant_id=tenant_id,
        knowledge_domain=scrape.domain,
        title=title,
        summary=summary,
        content=body,
        source_type=scrape.source_type,
        trust_level=scrape.trust_level,
        status="active",
        version=1,
        tags=[scrape.domain, "scrape_2026-06-06", _host(scrape.url)],
        owner="rag-scrape-agent-c",
    )


# ---------------------------------------------------------------------------
# Main scrape logic
# ---------------------------------------------------------------------------

async def _scrape_domain(
    *,
    domain: str,
    plan: dict,
    mcp: MiniMaxMCPClient,
    http: httpx.AsyncClient,
    seen_hashes: set[str],
    seen_urls: set[str],
    max_results: int,
) -> tuple[list[ScrapeResult], ScrapeReport]:
    """Run all queries for one domain, dedupe, return (results, report)."""
    report = ScrapeReport(domain=domain)
    collected: list[ScrapeResult] = []

    for query, weight in plan["queries"]:
        report.queries_run += 1
        try:
            search_hits = await mcp.search(query, max_results=max_results)
        except MCPCircuitOpen:
            logger.warning("MCP circuit open; aborting domain %s", domain)
            break
        except MCPError as e:
            logger.warning("MCP search failed for q=%r: %s", query, e)
            continue

        await asyncio.sleep(_SEARCH_DELAY_SECONDS)
        report.urls_discovered += len(search_hits)

        for hit in search_hits:
            url = (hit.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = (hit.get("title") or "").strip()
            snippet = (hit.get("snippet") or "").strip()

            body = ""
            fetch_ok = False
            fetch_error = ""
            if not _should_skip_fetch(url):
                try:
                    ftitle, body = await _fetch_body(http, url)
                    fetch_ok = True
                    report.urls_fetched += 1
                    if ftitle and not title:
                        title = ftitle
                    await asyncio.sleep(_FETCH_DELAY_SECONDS)
                except Exception as e:
                    fetch_error = type(e).__name__
                    logger.debug("fetch fail %s: %s", url, e)
            else:
                fetch_error = "skipped_domain"

            h = _content_hash(title, body or snippet)
            if h in seen_hashes:
                report.urls_deduped += 1
                continue
            seen_hashes.add(h)

            collected.append(ScrapeResult(
                url=url,
                title=title or url,
                snippet=snippet,
                body=body,
                domain=domain,
                query=query,
                trust_level=plan["trust_level"],
                source_type=plan["source_type"],
                fetch_ok=fetch_ok,
                fetch_error=fetch_error,
                content_hash=h,
            ))
            if len(report.sample_titles) < 5:
                report.sample_titles.append((title or url)[:80])

    return collected, report


async def run_scrape(
    *,
    domains: list[str],
    project_id: str,
    tenant_id: str,
    max_results: int,
) -> dict:
    """Top-level orchestrator. Returns a summary dict suitable for the report."""
    settings = get_settings()

    # Build the MCP client (with the corrected base URL — strip /v1)
    base_url = settings.minimax_base_url.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[: -3]

    mcp = MiniMaxMCPClient(
        api_key=settings.minimax_api_key,
        base_url=base_url,
        command=settings.minimax_mcp_command,
        args=settings.minimax_mcp_args,
        timeout=settings.minimax_mcp_timeout_seconds,
    )
    logger.info("Starting MCP client (base=%s)", base_url)
    await mcp.start()

    summary = {
        "started_at": time.time(),
        "domains": {},
        "totals": {
            "queries_run": 0,
            "urls_discovered": 0,
            "urls_fetched": 0,
            "urls_deduped": 0,
            "cards_ingested": 0,
            "cards_failed": 0,
        },
    }

    seen_hashes: set[str] = set()
    seen_urls: set[str] = set()

    embedding = KnowledgeEmbeddingService()
    ingestion = KnowledgeIngestionService(embedding_service=embedding)

    try:
        async with httpx.AsyncClient() as http:
            for domain in domains:
                if domain not in DOMAIN_PLANS:
                    logger.warning("Unknown domain %s; skipping", domain)
                    continue
                plan = DOMAIN_PLANS[domain]
                results, report = await _scrape_domain(
                    domain=domain,
                    plan=plan,
                    mcp=mcp,
                    http=http,
                    seen_hashes=seen_hashes,
                    seen_urls=seen_urls,
                    max_results=max_results,
                )

                for scrape in results:
                    try:
                        card = _to_card(scrape, project_id=project_id, tenant_id=tenant_id)
                        ingestion.create_card(card)
                        report.cards_ingested += 1
                    except Exception as e:
                        logger.warning("ingest failed: %s — %s", scrape.url, e)
                        report.cards_failed += 1

                summary["domains"][domain] = {
                    "label": plan["label"],
                    "queries_run": report.queries_run,
                    "urls_discovered": report.urls_discovered,
                    "urls_fetched": report.urls_fetched,
                    "urls_deduped": report.urls_deduped,
                    "cards_ingested": report.cards_ingested,
                    "cards_failed": report.cards_failed,
                    "sample_titles": report.sample_titles,
                }
                for k in summary["totals"]:
                    summary["totals"][k] += getattr(report, k)
                logger.info(
                    "domain=%s: %d cards ingested, %d failed, %d deduped",
                    domain, report.cards_ingested, report.cards_failed, report.urls_deduped,
                )
    finally:
        await mcp.stop()

    summary["finished_at"] = time.time()
    summary["duration_s"] = summary["finished_at"] - summary["started_at"]
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", default="default")
    p.add_argument("--tenant", default="default")
    p.add_argument(
        "--domains", nargs="+", default=list(DOMAIN_PLANS.keys()),
        choices=list(DOMAIN_PLANS.keys()),
    )
    p.add_argument(
        "--max-per-domain", type=int, default=15,
        help="max cards to ingest per domain (also caps MCP max_results)",
    )
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    summary = asyncio.run(run_scrape(
        domains=args.domains,
        project_id=args.project,
        tenant_id=args.tenant,
        max_results=args.max_per_domain,
    ))
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
