"""Tests for AdaptiveRouter — combines difficulty + load + project → ModelChoice."""

from __future__ import annotations

from app.services.router import AdaptiveRouter, ModelChoice


def test_easy_with_no_load_routes_to_e2b_bg():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    assert r.route(difficulty="easy", saturation={8080: 0.0, 8081: 0.0, 8082: 0.0}, project_hint=None) == ModelChoice.E2B_BG


def test_med_routes_to_e4b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    assert r.route(difficulty="med", saturation={8080: 0.0, 8081: 0.0, 8082: 0.0}, project_hint=None) == ModelChoice.E4B


def test_hard_with_idle_12b_routes_to_12b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    assert r.route(difficulty="hard", saturation={8080: 0.3, 8081: 0.0, 8082: 0.0}, project_hint=None) == ModelChoice.PRIMARY_12B


def test_hard_with_saturated_12b_falls_back_to_e4b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    # 12B at 0.9 (above 0.8 threshold), E4B at 0.3 (below 0.9)
    choice = r.route(difficulty="hard", saturation={8080: 0.9, 8081: 0.0, 8082: 0.3}, project_hint=None)
    assert choice == ModelChoice.E4B


def test_hard_with_saturated_12b_and_saturated_e4b_falls_back_to_e2b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    choice = r.route(difficulty="hard", saturation={8080: 0.95, 8081: 0.0, 8082: 0.95}, project_hint=None)
    assert choice == ModelChoice.E2B_BG


def test_med_with_saturated_e4b_falls_back_to_e2b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    choice = r.route(difficulty="med", saturation={8080: 0.0, 8081: 0.0, 8082: 0.95}, project_hint=None)
    assert choice == ModelChoice.E2B_BG


def test_ihi_project_always_uses_e2b_bg_regardless_of_difficulty():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    # Even hard with idle 12B, ihi project stays on E2B-bg
    choice = r.route(difficulty="hard", saturation={8080: 0.0, 8081: 0.0, 8082: 0.0}, project_hint="ihi")
    assert choice == ModelChoice.E2B_BG


def test_unknown_difficulty_defaults_to_e4b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    choice = r.route(difficulty="unknown", saturation={8080: 0.0, 8081: 0.0, 8082: 0.0}, project_hint=None)
    assert choice == ModelChoice.E4B
