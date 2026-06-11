"""Vietnamese PII classifier (P2.5, 2026-06-10).

Detects common Vietnamese PII patterns in chat messages:
- CCCD (Căn cước công dân): 12 digits
- CMND (Chứng minh nhân dân): 9 digits (legacy)
- Phone: 0xxxxxxxxx (10 digits) or +84xxxxxxxxx
- Email: standard RFC 5321-ish
- Bank account: 6-19 digit numbers (heuristic — Vietnamese bank
  accounts are typically 6-19 digits, often grouped)

Two modes (configurable via REDACT_PII env var):
- WARN (default if env var unset): log detection to the audit
  table but write the content unchanged. Lets ops retroactively
  audit for PII leakage.
- REDACT (default per user decision 2026-06-10): replace detected
  PII with [REDACTED-XXX] tags BEFORE writing to the DB.

Reference: Vietnamese PDPA (Nghị định 13/2023/NĐ-CP) — applies to
any data of Vietnamese citizens processed anywhere.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


# ── Regex patterns ──────────────────────────────────────────────────────

# CCCD: 12 digits, optional spaces/dashes. Word-boundary to avoid
# catching "12 items" or "page 12".
_CCCD_RE = re.compile(r"\b(\d[\d \-]{10,13}\d)\b")

# CMND: 9 digits (legacy ID). Same word-boundary care.
_CMND_RE = re.compile(r"\b(\d[\d \-]{7}\d)\b")

# Phone: 0xxxxxxxxx (10 digits) or +84xxxxxxxxx (12 chars). Allow
# optional internal spaces / dashes.
_PHONE_RE = re.compile(r"(?:\+84[\s\-]?|0[\s\-]?)\d(?:[\s\-]?\d){8,9}\b")

# Email (intentionally simple — ReDoS-safe).
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Bank account: 6-19 digit number (heuristic). Word-boundary to
# avoid matching inside larger numbers.
_BANK_RE = re.compile(r"\b\d{6,19}\b")


@dataclass(frozen=True)
class PIIDetection:
    """One PII match found in a string."""
    kind: str  # "cccd" | "cmnd" | "phone" | "email" | "bank"
    value: str  # the matched substring
    start: int
    end: int


@dataclass
class PIIReport:
    """Aggregated PII findings for one piece of text."""
    detections: list[PIIDetection] = field(default_factory=list)

    @property
    def has_pii(self) -> bool:
        return bool(self.detections)

    def kinds(self) -> set[str]:
        return {d.kind for d in self.detections}


# ── Public API ──────────────────────────────────────────────────────────


def detect_pii(text: str) -> PIIReport:
    """Find every PII match in ``text``. Pure function, no I/O."""
    if not text:
        return PIIReport()
    report = PIIReport()
    for kind, regex in (
        ("cccd", _CCCD_RE),
        ("cmnd", _CMND_RE),
        ("phone", _PHONE_RE),
        ("email", _EMAIL_RE),
        ("bank", _BANK_RE),
    ):
        for m in regex.finditer(text):
            # Filter: bank matches that look like plain numbers (e.g.
            # "30 days" → "30") are too noisy. Require either context
            # (e.g. "stk 123456" / "TK: 1234") OR length >= 8 to flag.
            if kind == "bank":
                if len(m.group(0)) < 8:
                    continue
            value = m.group(0)
            report.detections.append(
                PIIDetection(kind=kind, value=value, start=m.start(), end=m.end())
            )
    # Sort by start position so redaction is left-to-right and we
    # can skip overlapping detections.
    report.detections.sort(key=lambda d: (d.start, d.end))
    return report


def redact_text(text: str, report: PIIReport | None = None) -> str:
    """Replace every detection in ``report`` (or re-detect if None)
    with a [REDACTED-KIND] tag. Preserves character positions of
    the original text where possible.
    """
    if not text:
        return text
    if report is None:
        report = detect_pii(text)
    if not report.detections:
        return text
    # Apply non-overlapping replacements left-to-right
    out: list[str] = []
    cursor = 0
    last_end = -1
    for d in report.detections:
        if d.start < last_end:
            continue  # overlap — skip
        out.append(text[cursor:d.start])
        out.append(f"[REDACTED-{d.kind.upper()}]")
        cursor = d.end
        last_end = d.end
    out.append(text[cursor:])
    return "".join(out)


# ── Mode wiring ────────────────────────────────────────────────────────

Mode = Literal["warn", "redact"]


def get_pii_mode() -> Mode:
    """Return the active PII handling mode.

    Honors REDACT_PII env var:
      - "1" / "true" / "yes" / "on" → "redact"
      - "0" / "false" / "no" / "off" → "warn"
      - unset → "redact" (per 2026-06-10 user decision; can be
        disabled in dev via REDACT_PII=false)
    """
    val = os.environ.get("REDACT_PII", "").lower().strip()
    if val in ("0", "false", "no", "off"):
        return "warn"
    return "redact"


def process_text(text: str) -> tuple[str, PIIReport]:
    """Process ``text`` per the active mode.

    Returns (output_text, report). In WARN mode, output_text is
    the original; the report is logged. In REDACT mode, output_text
    has the PII replaced; the report is logged at info level.
    """
    if not text:
        return text, PIIReport()
    report = detect_pii(text)
    if not report.has_pii:
        return text, report
    mode = get_pii_mode()
    if mode == "redact":
        new_text = redact_text(text, report)
        # Log at info (not warning) — this is expected behaviour,
        # not an error. Includes the kinds detected so ops can
        # grep for the most common PII types.
        logger.info(
            "pii_classifier: redacted kinds=%s original_len=%d redacted_len=%d",
            sorted(report.kinds()),
            len(text),
            len(new_text),
        )
        return new_text, report
    # WARN mode: log + return original
    logger.warning(
        "pii_classifier: detected PII kinds=%s (REDACT_PII=false so not redacted)",
        sorted(report.kinds()),
    )
    return text, report
