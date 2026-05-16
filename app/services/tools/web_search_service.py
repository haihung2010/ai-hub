"""Web search service with robust fallback and relevance filtering."""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime
from typing import TypedDict
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse

import requests
from ddgs import DDGS
from lxml import html

logger = logging.getLogger(__name__)

HIGH_QUALITY_DOMAINS = {
    "anthropic.com",
    "platform.claude.com",
    "docs.python.org",
    "python.org",
    "developer.mozilla.org",
    "fastapi.tiangolo.com",
    "docs.pydantic.dev",
    "docs.docker.com",
    "kubernetes.io",
    "react.dev",
    "nextjs.org",
    "github.com",
    "sjc.com.vn",
    "pnj.com.vn",
    "doji.vn",
    "giavang.org",
    "chinhphu.vn",
    "moh.gov.vn",
    "moit.gov.vn",
    "sbv.gov.vn",
    "gso.gov.vn",
    "vnexpress.net",
    "tuoitre.vn",
    "thanhnien.vn",
    "vietnamnet.vn",
    "cafef.vn",
    "vietstock.vn",
}

LOW_QUALITY_DOMAINS = {
    "facebook.com",
    "pinterest.com",
    "quora.com",
    "reddit.com",
    "tiktok.com",
    "youtube.com",
}

SEARCH_ENGINE_HOSTS = {
    "bing.com",
    "duckduckgo.com",
    "google.com",
    "search.yahoo.com",
    "www.bing.com",
    "www.duckduckgo.com",
    "www.google.com",
}

TRACKING_PARAMS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref_src", "spm"}


class SearchResult(TypedDict):
    title: str
    url: str
    snippet: str


class WebSearchService:
    def __init__(
        self,
        timeout_seconds: float = 10.0,
        google_api_key: str = "",
        google_search_cx: str = "",
        searxng_base_url: str = "",
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._google_api_key = google_api_key
        self._google_search_cx = google_search_cx
        self._searxng_base_url = searxng_base_url.rstrip("/")

    def _host(self, url: str) -> str:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host

    def _canonical_url(self, url: str) -> str:
        parsed = urlparse(self._strip_tracking_params(url))
        path = parsed.path.rstrip("/")
        return parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), path=path, fragment="").geturl()

    def _query_terms(self, query: str) -> set[str]:
        return {term for term in re.findall(r"[\wÀ-ỹ]+", query.lower()) if len(term) > 2}

    def _decode_bing_redirect(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            if "bing.com" not in parsed.netloc:
                return url
            encoded = parse_qs(parsed.query).get("u", [""])[0]
            if not encoded.startswith("a1"):
                return url
            raw = encoded[2:]
            pad = "=" * ((4 - len(raw) % 4) % 4)
            decoded = base64.urlsafe_b64decode(raw + pad).decode("utf-8", errors="ignore")
            return decoded if decoded.startswith(("http://", "https://")) else url
        except Exception:
            return url

    def _strip_tracking_params(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            clean_qs = {
                k: v
                for k, v in qs.items()
                if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
            }
            return parsed._replace(query=urlencode(clean_qs, doseq=True)).geturl()
        except Exception:
            return url

    def _is_search_engine_page(self, url: str) -> bool:
        parsed = urlparse(url)
        host = self._host(url)
        if host not in SEARCH_ENGINE_HOSTS:
            return False
        return parsed.path in {"", "/", "/search", "/html/"} or parsed.path.startswith("/search")

    def _sanitize_url(self, raw_url: str) -> str:
        if not raw_url:
            return ""
        url = raw_url
        if "/l/?kh=" in url and "uddg=" in url:
            try:
                from urllib.parse import unquote

                url = unquote(url.split("uddg=")[1].split("&")[0])
            except Exception:
                pass
        candidate = self._decode_bing_redirect(url.strip())
        if not candidate.startswith(("http://", "https://")):
            if candidate.startswith("/"):
                return ""
            candidate = "https://" + candidate
        if "." not in candidate or len(candidate) < 10:
            return ""
        candidate = self._strip_tracking_params(candidate)
        if self._is_search_engine_page(candidate):
            return ""
        return candidate

    def _normalize_results(self, rows: list[dict], max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for row in rows:
            raw_href = str(row.get("href", row.get("url", "")))
            url = self._sanitize_url(raw_href)
            title = str(row.get("title", "")).strip()
            snippet = str(row.get("body", row.get("snippet", ""))).strip()
            if not url:
                continue
            canonical = self._canonical_url(url)
            if canonical in seen_urls:
                continue
            seen_urls.add(canonical)
            if not title:
                title = canonical
            results.append({"title": title, "url": canonical, "snippet": snippet[:500]})
            if len(results) >= max_results:
                break
        logger.info("Normalized into %d results", len(results))
        return results

    def _domain_score(self, url: str, query: str) -> int:
        host = self._host(url)
        score = 0
        if host in HIGH_QUALITY_DOMAINS or any(host.endswith(f".{domain}") for domain in HIGH_QUALITY_DOMAINS):
            score += 35
        if host.endswith((".gov", ".edu", ".gov.vn", ".edu.vn")):
            score += 25
        if host in LOW_QUALITY_DOMAINS or any(host.endswith(f".{domain}") for domain in LOW_QUALITY_DOMAINS):
            lowered = query.lower()
            if not any(term in lowered for term in ["reddit", "youtube", "video", "review", "community"]):
                score -= 25
        return score

    def _score_result(self, result: SearchResult, query: str, position: int) -> int:
        title = result["title"].lower()
        snippet = result["snippet"].lower()
        terms = self._query_terms(query)
        overlap = sum(1 for term in terms if term in title or term in snippet)
        score = max(0, 20 - position) + overlap * 4 + self._domain_score(result["url"], query)
        if not result["snippet"]:
            score -= 5
        if len(result["title"]) < 5:
            score -= 5
        return score

    def _rank_and_dedupe(self, results: list[SearchResult], query: str, max_results: int) -> list[SearchResult]:
        best_by_url: dict[str, tuple[int, SearchResult]] = {}
        for position, result in enumerate(results):
            canonical = self._canonical_url(result["url"])
            score = self._score_result(result, query, position)
            existing = best_by_url.get(canonical)
            if existing is None or score > existing[0]:
                best_by_url[canonical] = (score, {**result, "url": canonical})
        ranked = sorted(best_by_url.values(), key=lambda item: item[0], reverse=True)
        return [result for _, result in ranked[:max_results]]

    def _has_high_quality_result(self, results: list[SearchResult]) -> bool:
        return any(self._domain_score(result["url"], "") > 0 for result in results)

    def _safe_search(self, backend_name: str, search_fn, query: str, max_results: int) -> list[SearchResult]:
        try:
            return search_fn(query, max_results)
        except Exception as e:
            logger.warning("%s failed: %s", backend_name, e)
            return []

    def _search_ddgs(self, query: str, max_results: int) -> list[SearchResult]:
        try:
            with DDGS(timeout=self._timeout_seconds) as ddgs:
                rows = list(ddgs.text(query, region="wt-wt", safesearch="off", max_results=max_results))
                return self._normalize_results(rows, max_results)
        except Exception as e:
            logger.warning("DDGS failed: %s", e)
            return []

    def _search_searxng(self, query: str, max_results: int) -> list[SearchResult]:
        if not self._searxng_base_url:
            return []
        url = f"{self._searxng_base_url}/search"
        params = {"q": query, "format": "json", "language": "vi"}
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ai-hub/1.0)"}
        try:
            response = requests.get(url, params=params, headers=headers, timeout=self._timeout_seconds)
            response.raise_for_status()
            data = response.json()
            rows = data.get("results", []) or []
            normalized: list[SearchResult] = []
            for row in rows[: max_results * 2]:
                clean_url = self._sanitize_url(row.get("url", ""))
                if not clean_url:
                    continue
                normalized.append({
                    "title": (row.get("title") or "").strip(),
                    "url": clean_url,
                    "snippet": (row.get("content") or "").strip()[:500],
                })
                if len(normalized) >= max_results:
                    break
            logger.info("SearXNG returned %d results", len(normalized))
            return normalized
        except Exception as e:
            logger.warning("SearXNG failed: %s", type(e).__name__)
            return []

    def _parse_bing_page(self, query: str, max_results: int) -> list[dict]:
        q = query
        lowered = query.lower()
        if any(term in lowered for term in ["btc", "bitcoin", "crypto", "giá", "price"]):
            q = f"{query} price today"
        url = f"https://www.bing.com/search?q={quote_plus(q)}&setlang=vi-VN"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        try:
            response = requests.get(url, timeout=self._timeout_seconds, headers=headers)
            response.raise_for_status()
            doc = html.fromstring(response.text)
            items = doc.xpath('//li[contains(@class, "b_algo")]')
            if not items: items = doc.xpath('//h2/a')
            rows: list[dict] = []
            for item in items:
                if item.tag == 'a':
                    title, link, snippet = "".join(item.xpath('.//text()')).strip(), item.get('href', '').strip(), ""
                else:
                    t_nodes, l_nodes, s_nodes = item.xpath('.//h2//text()'), item.xpath('.//h2/a/@href'), item.xpath('.//p//text()')
                    if not t_nodes or not l_nodes: continue
                    title, link, snippet = "".join(t_nodes).strip(), l_nodes[0].strip(), "".join(s_nodes).strip()
                if title and link: rows.append({"title": title, "href": link, "body": snippet})
            return rows
        except Exception as e:
            logger.error("Bing parse failed: %s", e)
            return []

    def _search_google(self, query: str, max_results: int) -> list[SearchResult]:
        if not self._google_api_key or not self._google_search_cx:
            return []
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self._google_api_key,
            "cx": self._google_search_cx,
            "q": query,
            "num": min(max_results, 10),
            "lr": "lang_vi",
        }
        try:
            response = requests.get(url, params=params, timeout=self._timeout_seconds)
            response.raise_for_status()
            items = response.json().get("items", [])
            results: list[SearchResult] = []
            for item in items:
                url_clean = self._sanitize_url(item.get("link", ""))
                if not url_clean:
                    continue
                results.append({
                    "title": item.get("title", "").strip(),
                    "url": url_clean,
                    "snippet": item.get("snippet", "").strip()[:500],
                })
            logger.info("Google search returned %d results", len(results))
            return results
        except Exception as e:
            logger.warning("Google search failed: %s", type(e).__name__)
            return []

    def _is_date_query(self, query: str) -> bool:
        q = query.lower().strip()
        return any(x in q for x in ["ngày bao nhiêu", "ngay bao nhieu", "ngày mấy", "ngay may", "hôm nay là ngày", "hom nay la ngay", "today", "current date"])

    def _search_ddg_html(self, query: str, max_results: int) -> list[SearchResult]:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://duckduckgo.com/"}
        try:
            response = requests.get(url, timeout=self._timeout_seconds, headers=headers)
            response.raise_for_status()
            doc = html.fromstring(response.text)
            items = doc.xpath('//div[contains(@class, "result")]')
            logger.info("DDG HTML found %d items", len(items))
            rows: list[dict] = []
            for item in items:
                title_node = item.xpath('.//a[contains(@class, "result__a")]') or item.xpath(".//h2//a")
                snippet_node = item.xpath('.//a[contains(@class, "result__snippet")]') or item.xpath('.//*[contains(@class, "result__snippet") or contains(@class, "snippet")]')
                if not title_node:
                    continue
                title = "".join(title_node[0].xpath('.//text()')).strip()
                link = title_node[0].get('href', '').strip()
                snippet = "".join(snippet_node[0].xpath('.//text()')).strip() if snippet_node else ""
                if title and link: rows.append({"title": title, "href": link, "body": snippet})
            return self._normalize_results(rows, max_results)
        except Exception as e:
            logger.error("DDG HTML parse failed: %s", e)
            return []

    def _enhance_query(self, query: str) -> str:
        lowered = query.lower()
        if any(term in lowered for term in ["giá vàng", "gia vang", "sjc", "pnj", "doji"]):
            return f"{query} SJC PNJ DOJI giavang.org"
        return query

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not query.strip():
            return []
        if self._is_date_query(query):
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            return [{"title": "Current Time", "url": "https://time.is/", "snippet": f"Current local time: {now}"}]

        query = self._enhance_query(query)
        candidate_limit = min(max_results * 2, 10)
        candidates: list[SearchResult] = []

        candidates.extend(self._safe_search("SearXNG", self._search_searxng, query, candidate_limit))
        if len(candidates) >= max_results and self._has_high_quality_result(candidates):
            return self._rank_and_dedupe(candidates, query, max_results)

        candidates.extend(self._safe_search("Google search", self._search_google, query, candidate_limit))
        if len(candidates) >= max_results and self._has_high_quality_result(candidates):
            return self._rank_and_dedupe(candidates, query, max_results)

        candidates.extend(self._safe_search("DDGS", self._search_ddgs, query, candidate_limit))
        if len(candidates) < candidate_limit or not self._has_high_quality_result(candidates):
            candidates.extend(self._safe_search("DDG HTML", self._search_ddg_html, query, candidate_limit))
        if len(candidates) < max_results or not self._has_high_quality_result(candidates):
            rows = self._parse_bing_page(query, candidate_limit)
            candidates.extend(self._normalize_results(rows, candidate_limit))

        return self._rank_and_dedupe(candidates, query, max_results)
