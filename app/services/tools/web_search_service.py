"""Web search service with robust fallback and relevance filtering."""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import TypedDict
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse

import requests
from ddgs import DDGS
from lxml import html

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._google_api_key = google_api_key
        self._google_search_cx = google_search_cx

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
            clean_qs = {k: v for k, v in qs.items() if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}}
            return parsed._replace(query=urlencode(clean_qs, doseq=True)).geturl()
        except Exception:
            return url

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
            if candidate.startswith("/"): return ""
            candidate = "https://" + candidate
        if "." not in candidate or len(candidate) < 10:
            return ""
        return self._strip_tracking_params(candidate)

    def _normalize_results(self, rows: list[dict], max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        for row in rows:
            raw_href = str(row.get("href", row.get("url", "")))
            url = self._sanitize_url(raw_href)
            title = str(row.get("title", "")).strip()
            snippet = str(row.get("body", row.get("snippet", ""))).strip()
            if not url:
                continue
            if not title:
                title = url
            results.append({"title": title, "url": url, "snippet": snippet[:500]})
            if len(results) >= max_results:
                break
        logger.info("Normalized into %d results", len(results))
        return results

    def _search_ddgs(self, query: str, max_results: int) -> list[SearchResult]:
        try:
            with DDGS(timeout=self._timeout_seconds) as ddgs:
                rows = list(ddgs.text(query, region="wt-wt", safesearch="off", max_results=max_results))
                return self._normalize_results(rows, max_results)
        except Exception as e:
            logger.warning("DDGS failed: %s", e)
            return []

    def _parse_bing_page(self, query: str, max_results: int) -> list[dict]:
        q = query
        if any(t in query.lower() for t in ["btc", "bitcoin", "crypto", "gia"]):
            q = f"{query} price"
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
            logger.warning("Google search failed: %s", e)
            return []

    def _is_date_query(self, query: str) -> bool:
        q = query.lower().strip()
        return any(x in q for x in ["ngay bao nhieu", "ngay may", "today", "date"]) and len(q.split()) < 7

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
                title_node = item.xpath('.//a[contains(@class, "result__a")]')
                snippet_node = item.xpath('.//a[contains(@class, "result__snippet")]')
                if not title_node: continue
                title, link = "".join(title_node[0].xpath('.//text()')).strip(), title_node[0].get('href', '').strip()
                snippet = "".join(snippet_node[0].xpath('.//text()')).strip() if snippet_node else ""
                if title and link: rows.append({"title": title, "href": link, "body": snippet})
            return self._normalize_results(rows, max_results)
        except Exception as e:
            logger.error("DDG HTML parse failed: %s", e)
            return []

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not query.strip(): return []
        if self._is_date_query(query):
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            return [{"title": "Current Time", "url": "https://time.is/", "snippet": f"Current local time: {now}"}]
        results = self._search_google(query, max_results)
        if results: return results
        results = self._search_ddgs(query, max_results)
        if results: return results
        results = self._search_ddg_html(query, max_results)
        if results: return results
        return self._normalize_results(self._parse_bing_page(query, max_results), max_results)
