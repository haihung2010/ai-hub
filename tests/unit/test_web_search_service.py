"""Unit tests for WebSearchService utility methods."""

from __future__ import annotations

import base64
from unittest.mock import Mock, patch
from urllib.parse import quote

import pytest

from app.services.tools.web_search_service import WebSearchService


@pytest.fixture
def svc() -> WebSearchService:
    return WebSearchService(timeout_seconds=5.0, google_api_key="", google_search_cx="")


class TestDecodeBingRedirect:
    def test_non_bing_url_returned_as_is(self, svc: WebSearchService) -> None:
        url = "https://example.com/path?q=1"
        assert svc._decode_bing_redirect(url) == url

    def test_bing_url_without_u_param_returned_as_is(self, svc: WebSearchService) -> None:
        url = "https://www.bing.com/search?q=hello"
        assert svc._decode_bing_redirect(url) == url

    def test_bing_url_with_bad_prefix_returned_as_is(self, svc: WebSearchService) -> None:
        encoded = base64.urlsafe_b64encode(b"https://decoded.com").decode()
        url = f"https://www.bing.com/path?u=b1{encoded}"
        assert svc._decode_bing_redirect(url) == url

    def test_bing_redirect_decoded(self, svc: WebSearchService) -> None:
        target = "https://decoded.example.com/page"
        encoded = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        url = f"https://www.bing.com/path?u={encoded}"
        assert svc._decode_bing_redirect(url) == target


class TestStripTrackingParams:
    def test_removes_utm_params(self, svc: WebSearchService) -> None:
        url = "https://example.com/page?utm_source=google&utm_medium=cpc&foo=bar"
        result = svc._strip_tracking_params(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "foo=bar" in result

    def test_removes_fbclid(self, svc: WebSearchService) -> None:
        url = "https://example.com/?fbclid=ABC123&ref=test"
        result = svc._strip_tracking_params(url)
        assert "fbclid" not in result

    def test_removes_gclid(self, svc: WebSearchService) -> None:
        url = "https://example.com/?gclid=XYZ&page=1"
        result = svc._strip_tracking_params(url)
        assert "gclid" not in result
        assert "page=1" in result

    def test_clean_url_unchanged(self, svc: WebSearchService) -> None:
        url = "https://example.com/page?q=hello&page=2"
        result = svc._strip_tracking_params(url)
        assert "q=hello" in result
        assert "page=2" in result


class TestSanitizeUrl:
    def test_empty_returns_empty(self, svc: WebSearchService) -> None:
        assert svc._sanitize_url("") == ""

    def test_valid_https_url(self, svc: WebSearchService) -> None:
        result = svc._sanitize_url("https://example.com/page")
        assert result.startswith("https://")

    def test_path_only_returns_empty(self, svc: WebSearchService) -> None:
        assert svc._sanitize_url("/relative/path") == ""

    def test_no_scheme_gets_https(self, svc: WebSearchService) -> None:
        result = svc._sanitize_url("example.com/page")
        assert result.startswith("https://")

    def test_no_dot_returns_empty(self, svc: WebSearchService) -> None:
        assert svc._sanitize_url("nodothere") == ""

    def test_strips_utm_from_sanitized(self, svc: WebSearchService) -> None:
        url = "https://example.com/?utm_source=test&q=foo"
        result = svc._sanitize_url(url)
        assert "utm_source" not in result
        assert "q=foo" in result

    def test_ddg_uddg_redirect_decoded(self, svc: WebSearchService) -> None:
        target = "https://actual-target.com/page"
        url = f"https://duckduckgo.com/l/?kh=-1&uddg={quote(target)}&rut=x"
        result = svc._sanitize_url(url)
        assert "actual-target.com" in result

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.google.com/search?q=python",
            "https://www.bing.com/search?q=python",
            "https://duckduckgo.com/?q=python",
        ],
    )
    def test_search_engine_pages_return_empty(self, svc: WebSearchService, url: str) -> None:
        assert svc._sanitize_url(url) == ""


class TestNormalizeResults:
    def test_ddgs_style_rows(self, svc: WebSearchService) -> None:
        rows = [
            {"href": "https://a.com", "title": "A", "body": "snippet a"},
            {"href": "https://b.com", "title": "B", "body": "snippet b"},
        ]
        results = svc._normalize_results(rows, max_results=10)
        assert len(results) == 2
        assert results[0]["url"] == "https://a.com"
        assert results[0]["snippet"] == "snippet a"

    def test_max_results_respected(self, svc: WebSearchService) -> None:
        rows = [{"href": f"https://site{i}.com", "title": f"S{i}", "body": ""} for i in range(10)]
        assert len(svc._normalize_results(rows, max_results=3)) == 3

    def test_empty_url_rows_skipped(self, svc: WebSearchService) -> None:
        rows = [
            {"href": "", "title": "No URL", "body": ""},
            {"href": "https://valid.com", "title": "Valid", "body": ""},
        ]
        results = svc._normalize_results(rows, max_results=10)
        assert len(results) == 1
        assert results[0]["url"] == "https://valid.com"

    def test_missing_title_uses_url(self, svc: WebSearchService) -> None:
        rows = [{"href": "https://example.com", "title": "", "body": "x"}]
        results = svc._normalize_results(rows, max_results=10)
        assert results[0]["title"] == "https://example.com"

    def test_long_snippet_truncated_to_500(self, svc: WebSearchService) -> None:
        rows = [{"href": "https://example.com", "title": "T", "body": "x" * 600}]
        results = svc._normalize_results(rows, max_results=10)
        assert len(results[0]["snippet"]) == 500

    def test_url_key_used_as_fallback(self, svc: WebSearchService) -> None:
        rows = [{"url": "https://example.com", "title": "Example", "snippet": "search"}]
        results = svc._normalize_results(rows, max_results=10)
        assert len(results) == 1
        assert results[0]["url"] == "https://example.com"

    def test_duplicate_urls_are_skipped(self, svc: WebSearchService) -> None:
        rows = [
            {"href": "https://example.com/page?utm_source=x", "title": "Tracked", "body": "one"},
            {"href": "https://example.com/page", "title": "Clean", "body": "two"},
        ]

        results = svc._normalize_results(rows, max_results=10)

        assert len(results) == 1
        assert results[0]["url"] == "https://example.com/page"

    def test_empty_rows_returns_empty(self, svc: WebSearchService) -> None:
        assert svc._normalize_results([], max_results=10) == []


class TestRanking:
    def test_official_docs_rank_above_blog(self, svc: WebSearchService) -> None:
        results = [
            {
                "title": "Python version guide",
                "url": "https://random-blog.example/python-version",
                "snippet": "Python latest stable version guide",
            },
            {
                "title": "Python Downloads",
                "url": "https://www.python.org/downloads",
                "snippet": "Download the latest version of Python.",
            },
        ]

        ranked = svc._rank_and_dedupe(results, "latest Python stable version", max_results=2)

        assert ranked[0]["url"] == "https://www.python.org/downloads"

    def test_vietnamese_government_domain_ranks_above_weak_source(self, svc: WebSearchService) -> None:
        results = [
            {
                "title": "Tin tổng hợp quy định mới",
                "url": "https://example-news.test/quy-dinh",
                "snippet": "quy định mới nhất chính phủ",
            },
            {
                "title": "Cổng Thông tin điện tử Chính phủ",
                "url": "https://chinhphu.vn/quy-dinh-moi",
                "snippet": "quy định mới nhất chính phủ",
            },
        ]

        ranked = svc._rank_and_dedupe(results, "quy định mới nhất chính phủ", max_results=2)

        assert ranked[0]["url"] == "https://chinhphu.vn/quy-dinh-moi"

    def test_rank_and_dedupe_removes_tracking_duplicates(self, svc: WebSearchService) -> None:
        results = [
            {"title": "Tracked", "url": "https://example.com/page?utm_source=x#section", "snippet": "one"},
            {"title": "Clean", "url": "https://example.com/page", "snippet": "two"},
        ]

        ranked = svc._rank_and_dedupe(results, "example page", max_results=10)

        assert len(ranked) == 1
        assert ranked[0]["url"] == "https://example.com/page"


class TestSearchBackends:
    def test_date_query_returns_current_time(self, svc: WebSearchService) -> None:
        results = svc.search("today", max_results=3)

        assert results[0]["title"] == "Current Time"
        assert results[0]["url"] == "https://time.is/"

    def test_google_search_uses_custom_search_api(self) -> None:
        service = WebSearchService(
            timeout_seconds=5.0,
            google_api_key="test-key",
            google_search_cx="test-cx",
        )
        response = Mock()
        response.json.return_value = {
            "items": [
                {
                    "title": "Example",
                    "link": "https://example.com/?utm_source=x",
                    "snippet": "Snippet",
                }
            ]
        }

        with patch("app.services.tools.web_search_service.requests.get", return_value=response):
            results = service._search_google("vehix", max_results=5)

        response.raise_for_status.assert_called_once()
        assert results == [
            {
                "title": "Example",
                "url": "https://example.com/",
                "snippet": "Snippet",
            }
        ]

    def test_search_falls_back_to_ddg_html(self, svc: WebSearchService) -> None:
        with (
            patch.object(svc, "_search_google", return_value=[]),
            patch.object(svc, "_search_ddgs", return_value=[]),
            patch.object(
                svc,
                "_search_ddg_html",
                return_value=[{"title": "Fallback", "url": "https://fallback.com", "snippet": "ok"}],
            ),
            patch.object(svc, "_parse_bing_page", return_value=[]),
        ):
            results = svc.search("vehix pricing", max_results=5)

        assert results[0]["title"] == "Fallback"

    def test_search_keeps_better_free_fallback_result(self, svc: WebSearchService) -> None:
        with (
            patch.object(svc, "_search_google", return_value=[]),
            patch.object(
                svc,
                "_search_ddgs",
                return_value=[{"title": "Random post", "url": "https://reddit.com/r/python", "snippet": "latest Python"}],
            ),
            patch.object(
                svc,
                "_search_ddg_html",
                return_value=[{"title": "Python Downloads", "url": "https://www.python.org/downloads", "snippet": "latest Python"}],
            ),
            patch.object(svc, "_parse_bing_page", return_value=[]),
        ):
            results = svc.search("latest Python stable version", max_results=2)

        assert results[0]["url"] == "https://www.python.org/downloads"

    def test_search_continues_after_backend_exception(self, svc: WebSearchService) -> None:
        with (
            patch.object(svc, "_search_google", return_value=[]),
            patch.object(svc, "_search_ddgs", side_effect=RuntimeError("ddgs down")),
            patch.object(
                svc,
                "_search_ddg_html",
                return_value=[{"title": "Fallback", "url": "https://fallback.com", "snippet": "ok"}],
            ),
            patch.object(svc, "_parse_bing_page", return_value=[]),
        ):
            results = svc.search("vehix pricing", max_results=5)

        assert results[0]["url"] == "https://fallback.com"

    def test_bing_parser_extracts_rows(self, svc: WebSearchService) -> None:
        response = Mock()
        response.text = """
        <html><body><ol>
          <li class="b_algo"><h2><a href="https://example.com/a">Result A</a></h2><p>Text A</p></li>
        </ol></body></html>
        """

        with patch("app.services.tools.web_search_service.requests.get", return_value=response):
            rows = svc._parse_bing_page("vehix", max_results=5)

        response.raise_for_status.assert_called_once()
        assert rows == [{"title": "Result A", "href": "https://example.com/a", "body": "Text A"}]
