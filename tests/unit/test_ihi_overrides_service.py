"""Integration tests for ihi_overrides_service — uses real PG."""
import os
import pytest
from app.services.ihi_overrides_service import (
    get_active_override, set_override, delete_override, DeviceOverride,
)
from app.core.database import _get_pool

# This test requires PG. The conftest's autouse isolated_db fixture was modified
# in Task 1 to allow opt-out via `pytest.mark.no_isolated_db`, but this test
# NEEDS a real DB. Use a different marker or override the fixture here.
# Since the DB may not be reachable in test env, skip if not available.
try:
    from app.core.database import _get_pool as _try_pool
    _pool = _try_pool()
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False

pytestmark = [
    pytest.mark.skipif(not _DB_AVAILABLE, reason="PostgreSQL not reachable"),
    pytest.mark.no_isolated_db,
]


@pytest.fixture(scope="module")
def db_pool():
    pool = _get_pool()
    yield pool
    pool.close()


def test_set_then_get_override(db_pool):
    """Set override, then get it back."""
    set_override(
        db_pool, device_id="TEST-DEV-1", measurement="test_measurement",
        min_value=10.0, max_value=20.0, severity="DANGER",
        source="manual", set_by="pytest", note="test",
    )
    o = get_active_override(db_pool, "TEST-DEV-1", "test_measurement")
    assert o is not None
    assert o.min_value == 10.0
    assert o.max_value == 20.0
    assert o.severity == "DANGER"
    assert o.set_by == "pytest"
    # Cleanup
    delete_override(db_pool, "TEST-DEV-1", "test_measurement")


def test_set_override_upserts(db_pool):
    """Setting the same (device, measurement) twice updates, not duplicates."""
    set_override(db_pool, "TEST-DEV-2", "test_meas", 1.0, 2.0, "WARNING", "manual", "pytest", "first")
    set_override(db_pool, "TEST-DEV-2", "test_meas", 5.0, 6.0, "DANGER", "manual", "pytest", "second")
    o = get_active_override(db_pool, "TEST-DEV-2", "test_meas")
    assert o.min_value == 5.0
    assert o.severity == "DANGER"
    assert o.note == "second"
    delete_override(db_pool, "TEST-DEV-2", "test_meas")


def test_delete_override(db_pool):
    """Delete returns True if row existed, then get returns None."""
    set_override(db_pool, "TEST-DEV-3", "test_meas", 1.0, 2.0, "WARNING", "manual", "pytest", None)
    assert delete_override(db_pool, "TEST-DEV-3", "test_meas") is True
    assert get_active_override(db_pool, "TEST-DEV-3", "test_meas") is None
    # Deleting again returns False
    assert delete_override(db_pool, "TEST-DEV-3", "test_meas") is False
