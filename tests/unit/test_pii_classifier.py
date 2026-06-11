"""Unit tests for Vietnamese PII classifier (P2.5, 2026-06-10)."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


# ──────────────────────────────────────────────────────────────────────
# detect_pii — every PII kind
# ──────────────────────────────────────────────────────────────────────


def test_detect_phone_vietnamese_format() -> None:
    from app.services.pii_classifier import detect_pii
    r = detect_pii("SĐT của tôi là 0912345678")
    assert r.has_pii
    assert "phone" in r.kinds()


def test_detect_phone_with_country_code() -> None:
    from app.services.pii_classifier import detect_pii
    r = detect_pii("Call me at +84 901 234 567")
    assert "phone" in r.kinds()


def test_detect_cccd_12_digits() -> None:
    from app.services.pii_classifier import detect_pii
    r = detect_pii("Số CCCD: 012345678901")
    assert "cccd" in r.kinds()


def test_detect_cccd_with_spaces() -> None:
    from app.services.pii_classifier import detect_pii
    r = detect_pii("CCCD 123 456 789 012")
    assert "cccd" in r.kinds()


def test_detect_email() -> None:
    from app.services.pii_classifier import detect_pii
    r = detect_pii("Email tôi: anh.tuan@example.com")
    assert "email" in r.kinds()


def test_detect_bank_account() -> None:
    from app.services.pii_classifier import detect_pii
    r = detect_pii("STK: 1234567890123")
    assert "bank" in r.kinds()


def test_detect_no_pii_in_normal_text() -> None:
    from app.services.pii_classifier import detect_pii
    r = detect_pii("Bạn có thể giúp tôi đặt hàng không?")
    assert not r.has_pii


# ──────────────────────────────────────────────────────────────────────
# redact_text — replaces with [REDACTED-KIND] tags
# ──────────────────────────────────────────────────────────────────────


def test_redact_text_replaces_phone() -> None:
    from app.services.pii_classifier import redact_text
    out = redact_text("SĐT 0912345678 nhé")
    assert "0912345678" not in out
    assert "[REDACTED-PHONE]" in out


def test_redact_text_replaces_multiple_kinds() -> None:
    from app.services.pii_classifier import redact_text
    out = redact_text("Tôi tên A, SĐT 0912345678, email a@b.com")
    assert "0912345678" not in out
    assert "a@b.com" not in out
    assert "[REDACTED-PHONE]" in out
    assert "[REDACTED-EMAIL]" in out


def test_redact_text_preserves_non_pii_text() -> None:
    from app.services.pii_classifier import redact_text
    text = "Xin chào, bạn khỏe không?"
    assert redact_text(text) == text


def test_redact_text_empty_input() -> None:
    from app.services.pii_classifier import redact_text
    assert redact_text("") == ""


# ──────────────────────────────────────────────────────────────────────
# get_pii_mode — env var handling
# ──────────────────────────────────────────────────────────────────────


def test_default_mode_is_redact(monkeypatch) -> None:
    from app.services.pii_classifier import get_pii_mode
    monkeypatch.delenv("REDACT_PII", raising=False)
    assert get_pii_mode() == "redact"


def test_redact_pii_false_disables(monkeypatch) -> None:
    from app.services.pii_classifier import get_pii_mode
    monkeypatch.setenv("REDACT_PII", "false")
    assert get_pii_mode() == "warn"
    monkeypatch.setenv("REDACT_PII", "0")
    assert get_pii_mode() == "warn"
    monkeypatch.setenv("REDACT_PII", "off")
    assert get_pii_mode() == "warn"


def test_redact_pii_true_enables(monkeypatch) -> None:
    from app.services.pii_classifier import get_pii_mode
    monkeypatch.setenv("REDACT_PII", "true")
    assert get_pii_mode() == "redact"
    monkeypatch.setenv("REDACT_PII", "1")
    assert get_pii_mode() == "redact"


# ──────────────────────────────────────────────────────────────────────
# process_text — end-to-end with mode
# ──────────────────────────────────────────────────────────────────────


def test_process_text_redacts_in_redact_mode(monkeypatch) -> None:
    from app.services.pii_classifier import process_text
    monkeypatch.setenv("REDACT_PII", "true")
    out, report = process_text("SĐT 0912345678")
    assert "0912345678" not in out
    assert report.has_pii


def test_process_text_preserves_in_warn_mode(monkeypatch) -> None:
    from app.services.pii_classifier import process_text
    monkeypatch.setenv("REDACT_PII", "false")
    out, report = process_text("SĐT 0912345678")
    assert out == "SĐT 0912345678"  # unchanged in warn mode
    assert report.has_pii


def test_process_text_no_pii_passthrough(monkeypatch) -> None:
    from app.services.pii_classifier import process_text
    text = "Bạn có thể giúp tôi đặt hàng không?"
    out, report = process_text(text)
    assert out == text
    assert not report.has_pii


# ──────────────────────────────────────────────────────────────────────
# Integration: save_message actually redacts
# ──────────────────────────────────────────────────────────────────────


def test_save_message_redacts_pii_by_default(monkeypatch, client) -> None:
    """End-to-end: a chat with a phone number saves the redacted form
    to messages.content."""
    from app.core.database import get_db_connection
    from app.services.history_service import HistoryService
    import uuid as _uuid

    monkeypatch.setenv("REDACT_PII", "true")
    session_id = f"test-{_uuid.uuid4().hex[:8]}"
    # Create the session row first (FK constraint)
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (id, tenant_id, project_id) VALUES (%s, 'default', 'pii-test') "
            "ON CONFLICT (id) DO NOTHING",
            (session_id,),
        )
        conn.commit()

    svc = HistoryService()
    svc.save_message(
        session_id=session_id,
        role="user",
        content="SĐT của tôi là 0912345678",
        tenant_id="default",
    )
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM messages WHERE session_id = %s ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            )
            row = cur.fetchone()
        conn.commit()
    assert row is not None
    assert "0912345678" not in row["content"]
    assert "[REDACTED-PHONE]" in row["content"]


def test_save_message_preserves_in_warn_mode(monkeypatch, client) -> None:
    from app.core.database import get_db_connection
    from app.services.history_service import HistoryService
    import uuid as _uuid

    monkeypatch.setenv("REDACT_PII", "false")
    session_id = f"test-{_uuid.uuid4().hex[:8]}"
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (id, tenant_id, project_id) VALUES (%s, 'default', 'pii-test') "
            "ON CONFLICT (id) DO NOTHING",
            (session_id,),
        )
        conn.commit()

    svc = HistoryService()
    svc.save_message(
        session_id=session_id,
        role="user",
        content="SĐT của tôi là 0912345678",
        tenant_id="default",
    )
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM messages WHERE session_id = %s ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            )
            row = cur.fetchone()
        conn.commit()
    assert row is not None
    # In warn mode the content is unchanged
    assert "0912345678" in row["content"]
