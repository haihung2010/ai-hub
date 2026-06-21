"""Vietnamese LLM-as-judge for retrieval quality, using E4B Q4 (port 8081).

Scores a (query, retrieved_card_id, expected_card_id) triple on
relevance (0-3) and helpfulness (0-3). Runs via ai-hub's llama.cpp
E4B Q4 endpoint so no OpenAI API key is needed.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

PROMPT_PATH = Path(__file__).parent / "relevance_prompt.txt"
JUDGE_BASE_URL = os.environ.get("AIHUB_JUDGE_URL", "http://localhost:8081")
JUDGE_MODEL = os.environ.get("AIHUB_JUDGE_MODEL", "local-e4b-q4")
JUDGE_TIMEOUT = 30.0


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _call_e4b(prompt: str) -> str:
    """Call llama.cpp E4B Q4 via OpenAI-compat API."""
    body = json.dumps({
        "model": JUDGE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 256,
    }).encode()
    req = urllib.request.Request(
        f"{JUDGE_BASE_URL}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=JUDGE_TIMEOUT) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def _parse_json_score(text: str) -> dict:
    """Parse LLM response, tolerant of markdown code fences."""
    # Strip ```json ... ``` if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Find first {...} block
        match = re.search(r"\{[^}]*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def judge(query: str, retrieved_card_id: str, expected_card_id: str) -> dict:
    """Score a single retrieval result. Returns {relevance, helpfulness, explanation}."""
    template = _load_prompt()
    prompt = template.format(
        query=query,
        retrieved_card_id=retrieved_card_id or "(none)",
        expected_card_id=expected_card_id,
    )
    response = _call_e4b(prompt)
    scores = _parse_json_score(response)
    # Validate ranges
    scores["relevance"] = max(0, min(3, int(scores.get("relevance", 0))))
    scores["helpfulness"] = max(0, min(3, int(scores.get("helpfulness", 0))))
    return scores
