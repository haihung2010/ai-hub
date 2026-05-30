# app/services/ihi_validator.py
from dataclasses import dataclass
from typing import List, Optional
import json
import re

@dataclass
class ValidationResult:
    is_valid: bool
    danger: List[str]
    warning: List[str]
    normal_count: int
    error: Optional[str] = None
    is_empty: bool = False

class IHIValidator:
    """
    Validates and parses IHI sensor detection responses.
    Handles multiple JSON formats and ensures consistent output.
    """

    DANGER_THRESHOLD = {"temp": 90, "vib": 6.0, "current": 75}
    WARNING_THRESHOLD = {"temp": 85, "vib": 4.5, "current": 65}

    def parse(self, content: str) -> ValidationResult:
        """Parse IHI response content into structured result."""
        if not content or not content.strip():
            return ValidationResult(
                is_valid=False,
                danger=[],
                warning=[],
                normal_count=0,
                error="Empty response"
            )

        # Try to extract JSON from content
        json_str = self._extract_json(content)
        if not json_str:
            return ValidationResult(
                is_valid=False,
                danger=[],
                warning=[],
                normal_count=0,
                error="No valid JSON found"
            )

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return ValidationResult(
                is_valid=False,
                danger=[],
                warning=[],
                normal_count=0,
                error=f"JSON parse error: {e}"
            )

        # Handle different response formats
        danger, warning, normal_count = self._extract_results(data)

        is_empty = len(danger) == 0 and len(warning) == 0

        return ValidationResult(
            is_valid=True,
            danger=danger,
            warning=warning,
            normal_count=normal_count,
            is_empty=is_empty
        )

    def _extract_json(self, content: str) -> Optional[str]:
        """Extract JSON from content, handling markdown wrappers."""
        # Try direct parse first
        try:
            json.loads(content)
            return content
        except:
            pass

        # Try finding JSON in markdown code block
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            return json_match.group(1)

        # Try finding raw JSON object
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            return json_match.group(0)

        return None

    def _extract_results(self, data: dict) -> tuple:
        """Extract danger/warning/normal from parsed JSON."""
        danger = []
        warning = []
        normal_count = 0

        # Format: {"danger":[...], "warning":[...], "normal_count":N}
        if "danger" in data and "warning" in data:
            danger = data.get("danger", [])
            warning = data.get("warning", [])
            normal_count = data.get("normal_count", 0)

        # Format: {"abnormal": [{"device_id": "...", "reason": "..."}]}
        elif "abnormal" in data:
            abnormal_list = data.get("abnormal", [])
            if isinstance(abnormal_list, list):
                for item in abnormal_list:
                    if isinstance(item, dict):
                        device_id = item.get("device_id", "")
                        reason = item.get("reason", "").lower()
                        if any(x in reason for x in ["temp>90", "current>75", "vib>6"]):
                            danger.append(device_id)
                        elif any(x in reason for x in ["temp>85", "current>65", "vib>4.5"]):
                            warning.append(device_id)
                        else:
                            danger.append(device_id)  # Default to danger
                    elif isinstance(item, str):
                        danger.append(item)

        return danger, warning, normal_count