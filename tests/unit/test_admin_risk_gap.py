"""Tests for GET /v1/admin/risk/gap — failure-risk action gap endpoint.

The endpoint surfaces the "action gap": events where failure_risk
recommended an action but it was not applied. This is the leading
indicator for whether to enable actions (set FAILURE_RISK_LOG_ONLY=false
and FAILURE_RISK_ENABLE_ACTIONS=true).
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

# These tests rely on the autouse isolated_db fixture truncating the
# failure_risk_events table before each test, so each test starts clean.


def _insert_risk_event(
    *,
    conn,
    recommended_action: str,
    action_applied: int,
    risk_score: float = 0.5,
    risk_level: str = "medium",
    created_offset_minutes: int = 0,
) -> str:
    """Helper: insert a single failure_risk_event row."""
    record_id = f"risk_{uuid.uuid4().hex}"
    # user_id and session_id are NULLable FKs; set NULL to avoid FK
    # constraint on test sessions that don't exist in the truncated DB.
    conn.execute(
        """
        INSERT INTO failure_risk_events (
            id, tenant_id, project_id, user_id, session_id, risk_score,
            risk_level, risk_types_json, reasons_json, recommended_action,
            applied_action, action_applied, route_before, route_after,
            model_before, model_after, created_at
        ) VALUES (
            %s, 'default', 'test', NULL, NULL, %s, %s,
            '[]'::jsonb, '[]'::jsonb, %s, %s, %s,
            'local', 'local', 'gemma-4-12b', 'gemma-4-12b',
            NOW() - make_interval(mins => %s)
        )
        """,
        (
            record_id,
            risk_score,
            risk_level,
            recommended_action,
            recommended_action if action_applied else "none",
            action_applied,
            created_offset_minutes,
        ),
    )
    return record_id


def test_risk_gap_returns_mode_and_summary(client):
    """Endpoint must return the current risk mode and total counts."""
    # Insert a few risk events of varying action status
    from app.core.database import get_db_connection

    with get_db_connection() as conn:
        _insert_risk_event(
            conn=conn,
            recommended_action="enable_search",
            action_applied=0,
        )
        _insert_risk_event(
            conn=conn,
            recommended_action="enable_search",
            action_applied=0,
        )
        _insert_risk_event(
            conn=conn,
            recommended_action="ask_clarification",
            action_applied=0,
        )
        _insert_risk_event(
            conn=conn,
            recommended_action="none",
            action_applied=0,
        )
        conn.commit()

    resp = client.get("/v1/admin/risk/gap")
    assert resp.status_code == 200
    body = resp.json()

    # Mode reflects current settings
    assert "mode" in body
    assert "log_only" in body["mode"]
    assert "enable_actions" in body["mode"]
    assert "enable_search_action" in body["mode"]
    # Test conftest sets RATE_LIMIT_PER_MINUTE=5 but doesn't touch
    # failure_risk settings, so they should be at defaults.
    assert body["mode"]["log_only"] is True
    assert body["mode"]["enable_actions"] is False

    # Summary counts
    s = body["summary"]
    assert s["total_events"] == 4
    assert s["total_gap"] == 3  # 3 events had a recommended action, none applied
    assert s["gap_last_24h"] == 3
    assert s["action_enabled"] is False

    # Per-action breakdown
    pa = {p["recommended_action"]: p for p in body["per_action"]}
    assert pa["enable_search"]["total"] == 2
    assert pa["enable_search"]["applied"] == 0
    assert pa["enable_search"]["gap"] == 2
    assert pa["ask_clarification"]["total"] == 1
    assert pa["ask_clarification"]["gap"] == 1
    assert pa["none"]["total"] == 1
    assert pa["none"]["gap"] == 0  # 'none' doesn't count as a gap

    # Recommendation message reflects the disabled state
    assert "DISABLED" in body["recommendation"]


def test_risk_gap_handles_empty_table(client):
    """When no events have been recorded, the endpoint must not 500."""
    resp = client.get("/v1/admin/risk/gap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_events"] == 0
    assert body["summary"]["total_gap"] == 0
    assert body["summary"]["gap_last_24h"] == 0
    assert body["per_action"] == []


def test_risk_gap_24h_window_excludes_old_events(client):
    """Events older than 24h must not count toward gap_last_24h but
    should still count toward total_gap."""
    from app.core.database import get_db_connection

    with get_db_connection() as conn:
        # Recent event — in the 24h window
        _insert_risk_event(
            conn=conn,
            recommended_action="enable_search",
            action_applied=0,
            created_offset_minutes=10,  # 10 min ago
        )
        # Old event — outside 24h window
        _insert_risk_event(
            conn=conn,
            recommended_action="enable_search",
            action_applied=0,
            created_offset_minutes=60 * 25,  # 25 hours ago
        )
        conn.commit()

    resp = client.get("/v1/admin/risk/gap")
    body = resp.json()
    s = body["summary"]
    assert s["total_events"] == 2
    assert s["total_gap"] == 2  # both count toward total
    assert s["gap_last_24h"] == 1  # only the recent one


def test_risk_gap_applied_events_excluded_from_gap(client):
    """Events with action_applied=1 must not count toward the gap."""
    from app.core.database import get_db_connection

    with get_db_connection() as conn:
        # Recommended but not applied — gap
        _insert_risk_event(
            conn=conn,
            recommended_action="enable_search",
            action_applied=0,
        )
        # Recommended AND applied — no gap
        _insert_risk_event(
            conn=conn,
            recommended_action="enable_search",
            action_applied=1,
        )
        conn.commit()

    resp = client.get("/v1/admin/risk/gap")
    body = resp.json()
    pa = {p["recommended_action"]: p for p in body["per_action"]}
    assert pa["enable_search"]["total"] == 2
    assert pa["enable_search"]["applied"] == 1
    assert pa["enable_search"]["gap"] == 1
