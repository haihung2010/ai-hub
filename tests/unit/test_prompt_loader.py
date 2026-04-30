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
    assert p.model == "local-gemma4-e4b-q4"
    assert "AQI" in p.system_prompt


@pytest.mark.unit
def test_load_vehix() -> None:
    load_prompt.cache_clear()
    p = load_prompt("vehix")
    assert p.project_id == "vehix"
    assert p.enable_search is False
    assert "Vehix" in p.system_prompt


@pytest.mark.unit
def test_load_doden_disables_search() -> None:
    load_prompt.cache_clear()
    p = load_prompt("doden")
    assert p.project_id == "doden"
    assert p.enable_search is False
    assert "Doden" in p.system_prompt


@pytest.mark.unit
def test_load_stock_prediction() -> None:
    load_prompt.cache_clear()
    p = load_prompt("stock_prediction")
    assert p.project_id == "stock_prediction"
    assert p.provider == "local"
    assert p.temperature == 0.15
    assert "chứng khoán" in p.system_prompt
    assert "Không bịa" in p.system_prompt


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
