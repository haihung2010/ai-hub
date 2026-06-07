"""Tests for DifficultyClassifier heuristic scoring."""

from __future__ import annotations

from app.models.chat import ChatRequest
from app.services.difficulty_classifier import (
    DifficultyClassifier,
    classify_score,
)


def _req(text: str) -> ChatRequest:
    # model_construct bypasses Pydantic min_length=1 on user_message so the
    # empty-string test can exercise the classifier's empty-input branch.
    return ChatRequest.model_construct(
        user_name="t",
        user_message=text,
        project_id="t",
        history=[],
    )


class TestClassifyScore:
    def test_empty_message_is_easy(self):
        assert classify_score(0.0) == "easy"

    def test_below_easy_threshold_is_easy(self):
        assert classify_score(0.29) == "easy"

    def test_at_easy_threshold_is_easy(self):
        assert classify_score(0.3) == "easy"

    def test_just_above_easy_threshold_is_med(self):
        assert classify_score(0.31) == "med"

    def test_at_hard_threshold_is_med(self):
        assert classify_score(0.6) == "med"

    def test_above_hard_threshold_is_hard(self):
        assert classify_score(0.61) == "hard"

    def test_one_is_hard(self):
        assert classify_score(1.0) == "hard"


class TestScore:
    def test_short_message_low_score(self):
        clf = DifficultyClassifier()
        s = clf.score(_req("hello"))
        assert 0.0 <= s < 0.3

    def test_long_message_higher_score(self):
        clf = DifficultyClassifier()
        long_text = "x" * 2000
        s = clf.score(_req(long_text))
        assert s > 0.2

    def test_code_block_adds_to_score(self):
        clf = DifficultyClassifier()
        s_with = clf.score(_req("explain\n```python\nprint('hi')\n```"))
        s_without = clf.score(_req("explain print hi"))
        assert s_with > s_without

    def test_math_symbols_add_to_score(self):
        clf = DifficultyClassifier()
        s_with = clf.score(_req("calculate ∑ and √"))
        s_without = clf.score(_req("calculate sum and root"))
        assert s_with > s_without

    def test_multi_question_adds_to_score(self):
        clf = DifficultyClassifier()
        s_multi = clf.score(_req("what is X? and why? and how?"))
        s_single = clf.score(_req("what is X"))
        assert s_multi > s_single

    def test_empty_string_returns_zero(self):
        clf = DifficultyClassifier()
        assert clf.score(_req("")) == 0.0

    def test_classify_returns_string_label(self):
        clf = DifficultyClassifier()
        label = clf.classify(_req("any text"))
        assert label in ("easy", "med", "hard")
