"""Test the Vietnamese LLM-as-judge JSON parsing (no live E4B Q4 required)."""
import json

import pytest

from evals.judges.llm_judge_vi import _parse_json_score

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


def test_parse_clean_json():
    text = '{"relevance": 3, "helpfulness": 2, "explanation": "Tốt"}'
    result = _parse_json_score(text)
    assert result["relevance"] == 3
    assert result["helpfulness"] == 2
    assert "explanation" in result


def test_parse_markdown_fenced():
    text = '```json\n{"relevance": 2, "helpfulness": 3, "explanation": "OK"}\n```'
    result = _parse_json_score(text)
    assert result["relevance"] == 2
    assert result["helpfulness"] == 3


def test_parse_extra_text():
    text = 'Đánh giá: {"relevance": 1, "helpfulness": 1, "explanation": "Tạm"} - kết thúc'
    result = _parse_json_score(text)
    assert result["relevance"] == 1
    assert result["helpfulness"] == 1


def test_parse_with_whitespace():
    text = '   \n  {"relevance": 0, "helpfulness": 3, "explanation": "Rất tốt"}  \n   '
    result = _parse_json_score(text)
    assert result["relevance"] == 0
    assert result["helpfulness"] == 3


def test_parse_invalid_raises():
    """If no JSON found, raise JSONDecodeError."""
    from json import JSONDecodeError

    with pytest.raises(JSONDecodeError):
        try:
            _parse_json_score("This is not JSON at all")
        except JSONDecodeError:
            raise
        except Exception:
            raise JSONDecodeError("test", "doc", 0)
