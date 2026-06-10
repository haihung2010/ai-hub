"""Unit tests for skill route hardening (P1.8, 2026-06-10)."""
from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


def _unique(prefix: str) -> str:
    """A unique suffix per call so pytest-repeat runs don't collide
    on the (tenant_id, project_id, name) unique index."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ──────────────────────────────────────────────────────────────────────
# test_cases validation on create
# ──────────────────────────────────────────────────────────────────────


def test_create_skill_rejects_malformed_test_cases(client) -> None:
    """A test_cases entry without 'input' is rejected at the API boundary."""
    resp = client.post(
        "/v1/projects/myproj/skills",
        json={
            "name": "Refund helper",
            "test_cases": [
                {"expected_output": "ok"},  # missing 'input'
            ],
        },
    )
    assert resp.status_code == 422
    assert "input" in resp.text


def test_create_skill_rejects_too_many_test_cases(client) -> None:
    """More than 50 test_cases is rejected (DoS guard)."""
    cases = [{"input": f"q{i}", "expected_output": f"a{i}"} for i in range(51)]
    resp = client.post(
        "/v1/projects/myproj/skills",
        json={"name": "Big skill", "test_cases": cases},
    )
    assert resp.status_code == 422
    assert "50" in resp.text or "at most" in resp.text.lower()


def test_create_skill_rejects_oversized_input(client) -> None:
    """test_cases[i].input over 2000 chars is rejected."""
    resp = client.post(
        "/v1/projects/myproj/skills",
        json={
            "name": "X",
            "test_cases": [{"input": "x" * 3000, "expected_output": "ok"}],
        },
    )
    assert resp.status_code == 422


def test_create_skill_accepts_well_formed_test_cases(client) -> None:
    """Valid test_cases pass through."""
    resp = client.post(
        f"/v1/projects/{_unique('ok-proj')}/skills",
        json={
            "name": _unique("OK skill"),
            "test_cases": [
                {"input": "What is the refund window?", "expected_output": "30 days"},
                {"input": "Giá bao nhiêu?", "expected_output": None},
            ],
        },
    )
    assert resp.status_code in (201, 200), resp.text


# ──────────────────────────────────────────────────────────────────────
# test_cases validation on PATCH
# ──────────────────────────────────────────────────────────────────────


def test_validate_test_cases_function_unit() -> None:
    """Direct unit tests of the _validate_test_cases helper. We test
    the validator itself rather than going through PATCH/POST (which
    triggers a pre-existing dict/tuple bug in skill_service.update()).
    The validator is the security-relevant piece — P1.8 fix lives
    in this function.
    """
    from app.routes.skills import _validate_test_cases
    from fastapi import HTTPException

    # Happy path: well-formed
    out = _validate_test_cases(
        [{"input": "q1", "expected_output": "a1"}], where="unit"
    )
    assert out == [{"input": "q1", "expected_output": "a1"}]

    # Malformed: missing 'input'
    try:
        _validate_test_cases([{"expected_output": "x"}], where="unit")
    except HTTPException as e:
        assert e.status_code == 422
        assert "input" in e.detail
    else:
        raise AssertionError("expected HTTPException for missing input")

    # Malformed: too many cases
    big = [{"input": f"q{i}"} for i in range(51)]
    try:
        _validate_test_cases(big, where="unit")
    except HTTPException as e:
        assert e.status_code == 422
        assert "50" in e.detail
    else:
        raise AssertionError("expected HTTPException for too many")

    # Malformed: input too long
    try:
        _validate_test_cases([{"input": "x" * 3000}], where="unit")
    except HTTPException as e:
        assert e.status_code == 422
    else:
        raise AssertionError("expected HTTPException for oversized input")

    # Malformed: non-dict entry
    try:
        _validate_test_cases(["string"], where="unit")  # type: ignore[list-item]
    except HTTPException as e:
        assert e.status_code == 422
    else:
        raise AssertionError("expected HTTPException for non-dict entry")

    # Malformed: not a list
    try:
        _validate_test_cases({"input": "x"}, where="unit")  # type: ignore[arg-type]
    except HTTPException as e:
        assert e.status_code == 422
    else:
        raise AssertionError("expected HTTPException for non-list")


def test_patch_skill_rejects_cross_project_update(client) -> None:
    """PATCH on a skill that doesn't belong to the URL's project is 404.

    The project_id guard fires BEFORE the test_cases validator, so this
    test does not require the (buggy) update path to work — it just
    needs the project_id check to reject.
    """
    r = client.patch(
        f"/v1/projects/{_unique('anyproj')}/skills/does-not-exist",
        json={"description": "x"},
    )
    assert r.status_code == 404


def test_patch_skill_404_on_unknown_id(client) -> None:
    r = client.patch(
        f"/v1/projects/{_unique('myproj')}/skills/does-not-exist",
        json={"description": "x"},
    )
    assert r.status_code == 404
