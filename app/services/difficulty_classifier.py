"""Heuristic difficulty classifier for adaptive routing.

Phase 1: rule-based scoring using text signals (length, code, math,
multi-question, history depth).

Phase 2: replace with FastEmbed+LogisticRegression trained on
auto-labeled history. See `app/services/auto_labeler.py` for the
training pipeline (stub in Phase 1).
"""

from __future__ import annotations

from app.models.chat import ChatRequest


def classify_score(score: float) -> str:
    """Bucket a numeric score into easy/med/hard.

    Thresholds come from `Settings.difficulty_easy_threshold` and
    `Settings.difficulty_hard_threshold`; defaults are 0.3 and 0.6.
    Kept as a module-level function (not a method) so it's testable
    without instantiating the classifier.
    """
    if score <= 0.3:
        return "easy"
    if score <= 0.6:
        return "med"
    return "hard"


class DifficultyClassifier:
    """Heuristic difficulty classifier (Phase 1)."""

    def score(
        self,
        req: ChatRequest,
        history_count: int = 0,
    ) -> float:
        """Return a difficulty score in [0.0, 1.0].

        Args:
            req: The chat request.
            history_count: Number of prior messages in the conversation
                (used to weight long multi-turn contexts).
        """
        text = req.user_message
        if not text:
            return 0.0

        s = 0.0
        # Length signal (caps at 2000 chars ≈ 500 tokens)
        s += min(len(text) / 2000.0, 1.0) * 0.3

        # Code block signal
        if "```" in text or "    def " in text or "    class " in text:
            s += 0.3

        # Math signal
        if any(c in text for c in "∑∫√∂π≈≠≤≥"):
            s += 0.2

        # Multi-question signal
        if "?" in text and len(text.split("?")) > 2:
            s += 0.2

        # Multi-turn depth signal
        s += 0.1 * min(history_count / 10.0, 1.0)

        return min(s, 1.0)

    def classify(
        self,
        req: ChatRequest,
        history_count: int = 0,
    ) -> str:
        """Return one of: 'easy', 'med', 'hard'."""
        return classify_score(self.score(req, history_count))
