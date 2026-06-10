"""Log redaction filter (P0.3, 2026-06-10).

A logging.Filter that runs over every log record before it reaches a
handler and masks PII / secret values inside the formatted message.

Why: AI Hub logs occasionally include request bodies (e.g. when a
Chatwoot webhook is rejected for an invalid signature, the body may be
echoed for debugging). PII in those bodies — phone numbers, CCCD IDs,
emails — and credentials (API keys, bearer tokens) MUST NOT land in
plaintext in ``security.log`` or stdout.

Scope:
- Headers: ``X-API-KEY: <value>``, ``Authorization: Bearer <token>``,
  ``api_access_token: <value>`` (Chatwoot's convention).
- PII (Vietnamese): CCCD (12 digits, optionally space/dash separated),
  phone numbers (0xxxxxxxxx, +84xxxxxxxxx), email addresses.
- Long opaque tokens (32+ hex/alnum chars) — common API-key shape.

Disabled by default; enable with the env var ``LOG_REDACT_PII=true``.
The filter is also a no-op when the env var is unset so existing
deployments keep current behavior until they opt in.

Reference: Vietnamese PDPA (Nghị định 13/2023/NĐ-CP Art. 9) and the
security roadmap §P0.3.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any


# Header name + value: case-insensitive, captures the value (no trailing
# semicolons, since some headers chain values with commas). We do NOT
# include "Authorization" here — that's handled by _BEARER_RE, which
# needs the "Bearer" prefix to know the shape of the value to mask.
_HEADER_VALUE_RE = re.compile(
    r"((?:X-API-KEY|X-Chatwoot-Signature|X-Api-Key|api_access_token)"
    r"\s*[:=]\s*)([^\s,;]+)",
    re.IGNORECASE,
)

# Bearer token in the value half of an Authorization header.
_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-+/=]+)", re.IGNORECASE)

# Vietnamese CCCD / CMND: 9 or 12 digits, optional spaces/dashes.
# Anchored loosely so it doesn't catch a "12 items" count.
_CCCD_RE = re.compile(r"\b(\d[\d \-]{7,16}\d)\b")

# Vietnamese phone: 0xxxxxxxxx (10 digits) or +84xxxxxxxxx (12 chars).
# Allow optional internal spaces (e.g. "+84 901 234 567" or
# "0 901 234 567") so we catch both compact and human-formatted numbers.
_PHONE_RE = re.compile(r"(?:\+84[\s\-]?|0[\s\-]?)\d(?:[\s\-]?\d){8,9}\b")

# Email (very common case — keep the regex simple to avoid ReDoS).
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Long opaque tokens: 32+ alnum/hex chars, no spaces — typical of
# generated API keys. We are conservative: we only replace when the
# value is preceded by a likely-secret key name OR when it appears in
# a header value position. Bare 32+ char strings inside prose are NOT
# replaced (too many false positives).
_LONG_TOKEN_RE = re.compile(
    r"((?:api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*)([A-Za-z0-9_\-]{16,})",
    re.IGNORECASE,
)


def _is_enabled() -> bool:
    return os.environ.get("LOG_REDACT_PII", "").lower() in ("1", "true", "yes", "on")


def _redact_text(text: str) -> str:
    """Apply all redaction patterns to ``text``. Order matters."""
    # 1. Header values
    text = _HEADER_VALUE_RE.sub(
        lambda m: f"{m.group(1)}[REDACTED-KEY]" if m.group(2) else m.group(0),
        text,
    )
    # 2. Bearer tokens (after header values so Authorization is already half-redacted)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}[REDACTED-TOKEN]", text)
    # 3. Long opaque tokens
    text = _LONG_TOKEN_RE.sub(
        lambda m: f"{m.group(1)}[REDACTED-TOKEN]" if m.group(2) else m.group(0),
        text,
    )
    # 4. Email
    text = _EMAIL_RE.sub("[REDACTED-EMAIL]", text)
    # 5. Phone (Vietnamese)
    text = _PHONE_RE.sub("[REDACTED-PHONE]", text)
    # 6. CCCD (12 digits; do this last to avoid eating phone substrings
    #    that already matched above)
    text = _CCCD_RE.sub(
        lambda m: "[REDACTED-CCCD]" if len(re.sub(r"\D", "", m.group(1))) in (9, 12) else m.group(0),
        text,
    )
    return text


def _redact_arg(arg: Any) -> Any:
    """Redact a single log record arg. Strings are processed; other types pass through."""
    if isinstance(arg, str):
        return _redact_text(arg)
    return arg


def _redact_record_args(args: Any) -> tuple[Any, ...]:
    """Return a tuple of redacted args (preserves tuple form expected by logging)."""
    if args is None:
        return ()
    if isinstance(args, dict):
        return {k: _redact_arg(v) for k, v in args.items()}
    if isinstance(args, (tuple, list)):
        return tuple(_redact_arg(a) for a in args)
    return (_redact_arg(args),)


class LogRedactionFilter(logging.Filter):
    """logging.Filter that masks PII / secrets in record messages and args.

    Install on a handler (not a logger) so every record that reaches the
    handler is filtered exactly once. The filter is a no-op unless
    ``LOG_REDACT_PII`` is set to a truthy value.

    Example::

        handler.addFilter(LogRedactionFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not _is_enabled():
            return True
        try:
            # record.getMessage() formats msg + args, which is what handlers emit.
            # We rebuild msg/args so the redacted version is what gets written
            # by all formatters (including any that ignore getMessage()).
            if isinstance(record.msg, str):
                record.msg = _redact_text(record.msg)
            if record.args:
                record.args = _redact_record_args(record.args)
        except Exception:  # never let the filter break logging
            pass
        return True
