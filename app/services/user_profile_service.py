"""Aggregate structmem across all user sessions to derive user preferences.

For personalization on future purchases, we aggregate what we know about a user:
- sizes mentioned (M, L, ...)
- colors mentioned (trắng, xanh, ...)
- price range mentioned
- brands/products categories

Sync implementation: ai-hub's psycopg3 pool is sync (ConnectionPool,
not AsyncConnectionPool). Matches the rest of the codebase (orders_service.py,
verbatim_memory.py, history_service.py all use sync `with`).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


SIZE_RE = re.compile(
    r"\b(?:size\s+([xsmlxlXXL]+|\d+)|size\s+([XSMLXL]{1,3}))\b",
    re.IGNORECASE,
)
COLOR_RE = re.compile(
    r"\b(trắng|đen|xám|xanh|đỏ|vàng|hồng|be|nâu|navy)\b",
    re.IGNORECASE,
)
PRICE_RE = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})+|\d{4,7})\s*(?:k|000|đồng|vnđ|vnd|đ)?",
    re.IGNORECASE,
)


def _flatten_size_matches(matches):
    """SIZE_RE.findall returns a list of tuples (2 capture groups, both
    optional). Flatten to a list of non-empty match strings.
    """
    out: list[str] = []
    for m in matches:
        if isinstance(m, tuple):
            for g in m:
                if g:
                    out.append(g)
        elif m:
            out.append(m)
    return out


@dataclass
class UserPreferences:
    sizes: list[str]
    colors: list[str]
    price_max: int | None
    categories: list[str]


class UserProfileService:
    """Aggregate user preferences from structmem + messages across all sessions."""

    def __init__(self, db_pool):
        self.db = db_pool

    def get_preferences(self, tenant_id: str, user_id: str) -> UserPreferences:
        """Aggregate preferences from all structmem items + messages for user.

        Multi-tenant: filters by tenant_id to prevent cross-tenant leaks.
        """
        sizes: set[str] = set()
        colors: set[str] = set()
        prices: list[int] = []
        categories: set[str] = set()

        with self.db.connection() as conn:
            with conn.cursor() as cur:
                # Query structmem items
                cur.execute(
                    "SELECT subject, predicate, object, content FROM memory_items "
                    "WHERE tenant_id = %s AND user_id = %s ORDER BY created_at DESC LIMIT 100",
                    (tenant_id, user_id),
                )
                items = cur.fetchall()
                for it in items:
                    text = " ".join(
                        str(it.get(k) or "") for k in ("subject", "predicate", "object", "content")
                    )
                    sizes.update(s.lower() for s in _flatten_size_matches(SIZE_RE.findall(text)))
                    colors.update(c.lower() for c in COLOR_RE.findall(text))
                    for p in PRICE_RE.findall(text):
                        if p:
                            try:
                                prices.append(
                                    int(p.replace(".", "").replace(",", "").replace("k", "000"))
                                )
                            except (ValueError, TypeError):
                                continue
                # Also pull messages for additional context
                cur.execute(
                    "SELECT content FROM messages WHERE tenant_id = %s AND user_id = %s "
                    "AND role = 'user' ORDER BY created_at DESC LIMIT 50",
                    (tenant_id, user_id),
                )
                msgs = cur.fetchall()
                for m in msgs:
                    text = m.get("content") or ""
                    sizes.update(s.lower() for s in _flatten_size_matches(SIZE_RE.findall(text)))
                    colors.update(c.lower() for c in COLOR_RE.findall(text))
                    for p in PRICE_RE.findall(text):
                        if p:
                            try:
                                prices.append(
                                    int(p.replace(".", "").replace(",", "").replace("k", "000"))
                                )
                            except (ValueError, TypeError):
                                continue

        return UserPreferences(
            sizes=sorted(sizes),
            colors=sorted(colors),
            price_max=max(prices) if prices else None,
            categories=sorted(categories),
        )

    @staticmethod
    def format_for_context(prefs: UserPreferences) -> str:
        """Render preferences as a system prompt block for personalization.

        Returns empty string if no preferences could be extracted.
        """
        if not prefs.sizes and not prefs.colors and not prefs.price_max and not prefs.categories:
            return ""
        lines = ["<user_profile>"]
        if prefs.sizes:
            lines.append(f"Sizes mentioned: {', '.join(prefs.sizes)}")
        if prefs.colors:
            lines.append(f"Colors mentioned: {', '.join(prefs.colors)}")
        if prefs.price_max:
            lines.append(f"Price range observed: up to {prefs.price_max:,}đ")
        if prefs.categories:
            lines.append(f"Categories: {', '.join(prefs.categories)}")
        lines.append("</user_profile>")
        return "\n".join(lines)
