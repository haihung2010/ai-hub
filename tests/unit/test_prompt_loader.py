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
def test_unknown_project_falls_back_to_default() -> None:
    """Missing project files fall back to default.md (if it exists)."""
    load_prompt.cache_clear()
    p = load_prompt("this_project_does_not_exist")
    # Should get the default prompt instead of raising
    assert p.project_id == "this_project_does_not_exist"
    assert p.system_prompt  # default.md has content


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


@pytest.mark.unit
@pytest.mark.no_isolated_db
def test_default_prompt_instructs_order_lookup() -> None:
    """Default prompt must tell the LLM to use injected <order_lookup> data.

    Context: ai_service.py injects an <order_lookup> block into the system
    prompt when the user message contains an order code (ORD-XXXX). The
    injected block carries real order fields (product, size, color, price,
    status) from the orders table. Without explicit instructions the LLM
    echoes the order code from the user message and gives generic responses
    — observed in the e-commerce 100-user test (order_lookup_accuracy 0.4
    vs 0.9 target, 2026-06-14).

    This test pins the contract: every project that resolves to default.md
    (including unknown project_ids) must have an instruction the LLM can
    follow when it sees the <order_lookup> tag.
    """
    load_prompt.cache_clear()
    p = load_prompt("default")
    # 1) The injected tag must be referenced by name (string match, case-insensitive)
    assert "<order_lookup>" in p.system_prompt, (
        "default.md does not mention the <order_lookup> injected block. "
        "Add a section instructing the LLM to use its contents."
    )
    # 2) There must be an instruction to USE the data, not just mention the tag
    lowered = p.system_prompt.lower()
    use_phrases = [
        "dùng thông tin",
        "sử dụng thông tin",
        "dựa trên",
        "use the",
    ]
    assert any(phrase in lowered for phrase in use_phrases), (
        f"default.md must include a 'use the data' instruction. "
        f"Expected one of {use_phrases}, but got prompt:\n{p.system_prompt}"
    )
