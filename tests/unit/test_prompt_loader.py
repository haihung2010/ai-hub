"""Prompt loader handles valid projects, rejects unknowns, guards traversal."""

from __future__ import annotations

import pytest

from app.core.errors import ProjectNotFound
from app.prompts.loader import load_prompt


@pytest.mark.unit
def test_load_iot() -> None:
    load_prompt.cache_clear()
    p = load_prompt("iot")
    assert p.project_id == "iot"
    assert p.provider == "local"
    assert p.model.startswith("VladimirGav/")
    assert "AQI" in p.system_prompt


@pytest.mark.unit
def test_load_vehix() -> None:
    load_prompt.cache_clear()
    p = load_prompt("vehix")
    assert p.project_id == "vehix"
    assert "Vehix" in p.system_prompt


@pytest.mark.unit
def test_unknown_project_raises() -> None:
    load_prompt.cache_clear()
    with pytest.raises(ProjectNotFound):
        load_prompt("nope")


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["../secret", "iot/evil", "", " ", "a b", "foo.bar"])
def test_path_traversal_blocked(bad: str) -> None:
    load_prompt.cache_clear()
    with pytest.raises(ProjectNotFound):
        load_prompt(bad)


@pytest.mark.unit
def test_cache_returns_same_instance() -> None:
    load_prompt.cache_clear()
    a = load_prompt("iot")
    b = load_prompt("iot")
    assert a is b
