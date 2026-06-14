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
    r"\b(\d{1,3}(?:[.,]\d{3})+|\d{1,7}(?:k)?)\b",
    re.IGNORECASE,
)


def _looks_like_price(token: str) -> bool:
    """Heuristic: decide whether a PRICE_RE match is a real price vs. an
    order-code fragment like "34880" (from "ORD-_000-34880") or a memory
    triple serial id.

    Accept if any of:
      - has a thousands separator (. or ,) — e.g. "250.000", "1,200,000"
      - has the "k" suffix — e.g. "250k", "1200k"
      - 1-3 digits (clearly a "Xk" or "X00" shorthand) — e.g. "250", "99"
      - 4-5 digits ending in 000 or 500 (typical round VND amounts)
      - 6+ digits ending in 4+ zeros (large round VND amounts)

    Reject if:
      - 4-5 digits not ending in a round pattern (order-code-like)
      - any digits not matching the above
    """
    if not token:
        return False
    lower = token.lower()
    if "." in lower or "," in lower:
        return True
    if lower.endswith("k"):
        return True
    if len(lower) <= 3:
        return True
    # 4-5 digits: accept only if it ends in 000 or 500 (round VND)
    if len(lower) in (4, 5) and (lower.endswith("000") or lower.endswith("500")):
        return True
    # 6+ digits: require 4+ trailing zeros
    if len(lower) >= 6 and lower.endswith(("0000", "00000", "000000", "0000000")):
        return True
    return False


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
                            # Skip bare 5-7 digit numbers that look like order codes
                            # (e.g. "34880" from "ORD-_000-34880"). Require either
                            # the "k" suffix, a thousands separator, or a
                            # currency hint to count as a real price.
                            if not _looks_like_price(p):
                                continue
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
                            if not _looks_like_price(p):
                                continue
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
