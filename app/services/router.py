"""AdaptiveRouter — combines difficulty + load + project → ModelChoice.

Decision flow (see spec §4.3):
  1. difficulty → preferred model (easy=E2B-bg, med=E4B, hard=12B)
  2. load-aware degradation:
     - if preferred=12B AND 12B saturated > threshold:
         - if hard AND E4B idle → E4B
         - else → E2B-bg
     - if preferred=E4B AND E4B saturated > threshold → E2B-bg
  3. project override:
     - if project_hint="ihi" → always E2B-bg
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class ModelChoice(str, Enum):
    """Which model the router selected. Maps to actual model aliases."""

    E2B_BG = "local-gemma4-e2b-q4-bg"     # port 8081
    E4B = "local-gemma4-e4b-q4"             # port 8082
    PRIMARY_12B = "local-gemma4-12b-q4-text"  # port 8080


class AdaptiveRouter:
    def __init__(
        self,
        *,
        difficulty_easy_threshold: float = 0.3,
        difficulty_hard_threshold: float = 0.6,
        saturation_12b_degrade: float = 0.8,
        saturation_e4b_degrade: float = 0.9,
    ) -> None:
        self._easy_t = difficulty_easy_threshold
        self._hard_t = difficulty_hard_threshold
        self._12b_t = saturation_12b_degrade
        self._e4b_t = saturation_e4b_degrade

    def route(
        self,
        *,
        difficulty: str,
        saturation: dict[int, float],
        project_hint: Optional[str] = None,
    ) -> ModelChoice:
        # Project override (IHI always E2B-bg)
        if project_hint == "ihi":
            return ModelChoice.E2B_BG

        # Step 1: preferred model
        if difficulty == "easy":
            preferred = ModelChoice.E2B_BG
        elif difficulty == "med":
            preferred = ModelChoice.E4B
        elif difficulty == "hard":
            preferred = ModelChoice.PRIMARY_12B
        else:
            # Unknown difficulty → default to E4B
            return ModelChoice.E4B

        # Step 2: load-aware degradation
        if preferred == ModelChoice.PRIMARY_12B:
            sat_12b = saturation.get(8080, 0.0)
            sat_e4b = saturation.get(8082, 0.0)
            if sat_12b > self._12b_t:
                if difficulty == "hard" and sat_e4b < self._e4b_t:
                    return ModelChoice.E4B
                return ModelChoice.E2B_BG
        elif preferred == ModelChoice.E4B:
            sat_e4b = saturation.get(8082, 0.0)
            if sat_e4b > self._e4b_t:
                return ModelChoice.E2B_BG

        return preferred
