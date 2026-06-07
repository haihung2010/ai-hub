"""Tests for LoadMonitor — probes llama-server /health?include_slots=1."""

from __future__ import annotations

import pytest

from app.services.load_monitor import LoadMonitor, _parse_saturation


def test_parse_saturation_all_idle():
    payload = {"slots": [{"state": 0}, {"state": 0}, {"state": 0}, {"state": 0}]}
    assert _parse_saturation(payload) == 0.0


def test_parse_saturation_all_busy():
    payload = {"slots": [{"state": 1}, {"state": 1}, {"state": 1}, {"state": 1}]}
    assert _parse_saturation(payload) == 1.0


def test_parse_saturation_half_busy():
    payload = {"slots": [{"state": 1}, {"state": 0}, {"state": 1}, {"state": 0}]}
    assert _parse_saturation(payload) == 0.5


def test_parse_saturation_no_slots():
    assert _parse_saturation({"slots": []}) == 0.0


def test_parse_saturation_missing_slots_key():
    assert _parse_saturation({}) == 0.0


class TestLoadMonitorCache:
    def test_first_probe_caches_result(self):
        mon = LoadMonitor()
        # Set cache directly to test cache logic without real network
        mon._cache[8080] = (0.5, 100.0)  # (saturation, expiry)
        sat = mon.get_saturation(8080, probe_fn=lambda url: {"slots": []})
        assert sat == 0.5  # cached, probe_fn not called

    def test_expired_cache_triggers_probe(self):
        mon = LoadMonitor(cache_ttl_seconds=0.01)
        mon._cache[8080] = (0.5, 0.0)  # already expired
        called = []
        def fake_probe(url):
            called.append(url)
            return {"slots": [{"state": 1}, {"state": 0}]}
        sat = mon.get_saturation(8080, probe_fn=fake_probe)
        assert sat == 0.5
        assert len(called) == 1

    def test_probe_error_returns_zero(self):
        mon = LoadMonitor()
        def bad_probe(url):
            raise ConnectionError("refused")
        # Should not raise; should return 0.0 (assume idle)
        sat = mon.get_saturation(8080, probe_fn=bad_probe)
        assert sat == 0.0
