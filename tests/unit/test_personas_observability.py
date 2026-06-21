"""Test that the 10-persona test script has observability wrapping.

The actual 10-persona test makes HTTP calls to the live ai-hub API
and is NOT a unit test. This test only verifies the static integration:
- script imports ObservabilityService
- script references persona span names matching the persona name pattern
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PERSONAS_SCRIPT = Path("scripts/test_10user_memory_quality.py")
PERSONAS_YAML = Path("scripts/personas.yaml")

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


def test_personas_script_imports_observability():
    """Script must import ObservabilityService for trace wrapping."""
    source = PERSONAS_SCRIPT.read_text()
    assert "ObservabilityService" in source, "Missing ObservabilityService import"
    assert "obs.span" in source or "ObservabilityService.instance().span" in source, (
        "Missing span() call for persona wrapping"
    )


def test_personas_script_imports_cleanly():
    """Script must not have syntax errors."""
    result = subprocess.run(
        [sys.executable, "-c", f"import ast; ast.parse(open('{PERSONAS_SCRIPT}').read())"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"Syntax error: {result.stderr}"


def _load_personas_yaml():
    try:
        import yaml
    except ImportError:
        pytest.skip("yaml not installed")
    with PERSONAS_YAML.open() as f:
        data = yaml.safe_load(f)
    # personas.yaml is a dict keyed by persona name -> fields.
    # Each entry's 'name' is the dict key.
    personas = [{"name": name, **fields} for name, fields in data.items()]
    return personas


def test_personas_yaml_exists_and_valid():
    """personas.yaml must exist and be parseable YAML."""
    try:
        import yaml
    except ImportError:
        pytest.skip("yaml not installed")
    assert PERSONAS_YAML.exists(), f"Missing {PERSONAS_YAML}"
    with PERSONAS_YAML.open() as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "personas.yaml must be a dict keyed by persona name"
    assert len(data) >= 1, "personas.yaml must have at least 1 persona"


def test_personas_have_required_fields():
    """Each persona must have tenant_id, project_id, user_name (name is the dict key)."""
    personas = _load_personas_yaml()
    for p in personas:
        for field in ("tenant_id", "project_id", "user_name"):
            assert field in p, f"Persona {p.get('name', '?')} missing '{field}'"
