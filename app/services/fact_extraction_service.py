"""Lightweight fact extraction for fanpage chatbot - simpler and faster than StructMem."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.database import DEFAULT_TENANT_ID, get_db_connection
from app.models.chat import Message

logger = logging.getLogger(__name__)

FANPAGE_FACT_EXTRACTION_PROMPT = """Extract key facts from this conversation for a fanpage chatbot.
Return strict JSON with a single key "facts" mapping to a list of objects with:
- fact: The actual fact (1-2 sentences, clear and specific)
- category: One of: preference, behavior, interest, context, other
- confidence: 0.0-1.0 confidence score

Only extract durable, reusable facts. Ignore greetings, chit-chat, and temporary context.
Focus on user preferences, interests, and important context about the user or fanpage.
"""


class FactExtractionService:
    def _build_source_text(self, messages: Sequence[tuple[int, Message]]) -> str:
        return "\n".join(f"{message.role}: {message.content}" for _, message in messages)

    def _build_prompt(self, messages: Sequence[tuple[int, Message]]) -> list[Message]:
        return [
            Message(role="system", content=FANPAGE_FACT_EXTRACTION_PROMPT),
            Message(role="user", content=self._build_source_text(messages)),
        ]

    def _parse_payload(self, payload: str) -> list[dict]:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Fact extraction returned invalid JSON")
            return []

        facts = parsed.get("facts", [])
        return facts if isinstance(facts, list) else []

    def _insert_facts(
        self,
        *,
        user_id: str,
        tenant_id: str,
        project_id: str,
        session_id: str,
        facts: list[dict],
    ) -> int:
        if not facts:
            return 0

        with get_db_connection() as conn:
            count = 0
            for fact in facts:
                fact_text = str(fact.get("fact", "")).strip()
                if not fact_text:
                    continue
                conn.execute(
                    "INSERT INTO fanpage_facts (id, user_id, tenant_id, project_id, session_id, fact, category, confidence) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        str(uuid.uuid4()),
                        user_id,
                        tenant_id,
                        project_id,
                        session_id,
                        fact_text,
                        fact.get("category", "other"),
                        float(fact.get("confidence", 0.5) or 0.5),
                    ),
                )
                count += 1
            conn.commit()
        return count

    async def extract_and_store(
        self,
        *,
        user_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        project_id: str,
        session_id: str,
        messages: Sequence[tuple[int, Message]],
        provider,
        model: str,
    ) -> int:
        if not messages:
            return 0

        prompt_messages = self._build_prompt(messages)
        payload = await provider.complete(prompt_messages, model, 0.2)
        facts = self._parse_payload(payload)
        count = self._insert_facts(
            user_id=user_id,
            tenant_id=tenant_id,
            project_id=project_id,
            session_id=session_id,
            facts=facts,
        )
        logger.info(
            "Extracted and stored %d facts user=%s project=%s session=%s",
            count,
            user_id,
            project_id,
            session_id,
        )
        return count


# ── Pinned-memory auto-extractor (Mem0 pattern) ───────────────────────
#
# A second pass on top of fanpage_facts: extract atomic, durable user
# preferences and store them in pinned_memories so they show up in the
# system prompt for every future turn (alongside explicit "hãy nhớ ..."
# reminders from the user).
#
# Why a separate class instead of overloading FactExtractionService:
# - different schema (pinned_memories vs fanpage_facts)
# - different prompt shape (key/value vs prose fact)
# - different dedupe semantics (key match vs insertion always)
# - synchronous (caller wraps in async if needed) — easier to test
#
# Trigger: call ``extract_and_persist`` after a meaningful turn boundary
# (every N turns, on /new boundary, or via a periodic background job).


_PINNED_EXTRACTION_PROMPT = """Bạn là bộ trích xuất thông tin cá nhân từ cuộc hội thoại tiếng Việt.
Nhiệm vụ: tìm các thông tin THẬT SỰ BỀN VỮNG về người dùng (sở thích, thói quen, nghề nghiệp, nơi sống, gia đình, mục tiêu, sở thích tiêu dùng).

Trả lời BẰNG JSON THUẦN (không giải thích, không markdown):
[
  {"key": "ten_viet_tat_hoac_dinh_danh", "value": "giá trị thực tế", "confidence": 0.0-1.0}
]

QUY TẮC:
- CHỈ trích thông tin có thật trong cuộc hội thoại. KHÔNG bịa.
- BỎ QUA chào hỏi, cảm xúc thoáng qua, câu hỏi tạm thời.
- Mỗi thông tin có 1 key ngắn gọn (danh từ, không quá 4 từ, viết thường, dấu cách).
- value là giá trị thực tế (có thể 1-2 câu).
- confidence: 0.9+ nếu user nói rõ ràng, 0.6-0.8 nếu suy ra, <0.6 thì bỏ qua.
- Nếu không có thông tin bền vững nào, trả về [].

Ví dụ input:
User: "Tôi tên An, sống ở Đà Nẵng, thích uống cà phê đen buổi sáng."
Assistant: "Chào An!"

Output:
[{"key":"ho_ten","value":"An","confidence":0.95},{"key":"noi_song","value":"Đà Nẵng","confidence":0.95},{"key":"thich_uong","value":"cà phê đen buổi sáng","confidence":0.85}]"""


@dataclass(frozen=True)
class ExtractedFact:
    """One atomic fact extracted by the LLM."""

    key: str
    value: str
    confidence: float


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of ``PinnedMemoryAutoExtractor.extract_and_persist``."""

    added: int
    dropped: int
    raw_count: int  # facts the LLM produced before dedupe


# Module-level helpers — pure, easy to test in isolation

def _parse_llm_facts(text: str) -> list[ExtractedFact]:
    """Parse the LLM JSON response into a list of facts.

    Tolerant of prose wrapping the JSON: scans for the first '[' and the
    matching ']' and tries to parse the slice. Malformed inputs return [].
    """
    if not text or not text.strip():
        return []

    # Try direct parse first
    candidate = text.strip()
    try:
        return _facts_from_json(candidate)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Fallback: extract the first JSON array
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start >= 0 and end > start:
        try:
            return _facts_from_json(candidate[start : end + 1])
        except (json.JSONDecodeError, ValueError, TypeError):
            return []
    return []


def _facts_from_json(raw: str) -> list[ExtractedFact]:
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        return []
    facts: list[ExtractedFact] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        key = re.sub(r"\s+", " ", str(entry.get("key", "")).strip().lower())
        value = str(entry.get("value", "")).strip()
        if not key or not value:
            continue
        try:
            confidence = float(entry.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        # Clamp to [0, 1]
        confidence = max(0.0, min(1.0, confidence))
        # Drop very-low-confidence noise
        if confidence < 0.6:
            continue
        facts.append(ExtractedFact(key=key[:120], value=value[:1000], confidence=confidence))
    return facts


def _dedupe_against_existing(
    facts: list[ExtractedFact],
    existing_keys: Sequence[tuple[str, str]],
) -> tuple[list[ExtractedFact], list[ExtractedFact]]:
    """Drop facts whose key already exists as an active pinned memory.

    Returns ``(kept, dropped)``. Simple key equality is enough at the moment;
    future enhancement could use embedding similarity for near-duplicates.
    """
    seen = {key for key, _ in existing_keys}
    kept: list[ExtractedFact] = []
    dropped: list[ExtractedFact] = []
    for fact in facts:
        if fact.key in seen:
            dropped.append(fact)
        else:
            kept.append(fact)
    return kept, dropped


class PinnedMemoryAutoExtractor:
    """Extract durable user facts from recent messages and write them to
    the existing ``pinned_memories`` table with ``scope="auto"``.

    The ``llm`` argument is any object with a synchronous ``extract(messages) -> str``
    method (returning the raw LLM response). Use a small model (E2B Q4)
    for cost / latency; the extraction prompt is structured enough that
    small models cope well.
    """

    def __init__(
        self,
        *,
        llm: object,
        pinned: object,
        min_confidence: float = 0.6,
    ) -> None:
        self._llm = llm
        self._pinned = pinned
        self._min_confidence = min_confidence

    def extract_and_persist(
        self,
        *,
        tenant_id: str,
        project_id: str,
        user_id: str,
        messages: Sequence[Message],
        session_id: str | None = None,
    ) -> ExtractionResult:
        if not messages:
            return ExtractionResult(added=0, dropped=0, raw_count=0)

        raw_response = self._llm.extract(list(messages))  # type: ignore[attr-defined]
        facts = _parse_llm_facts(raw_response)
        facts = [f for f in facts if f.confidence >= self._min_confidence]

        existing = self._existing_keys(tenant_id, project_id, user_id)
        kept, dropped = _dedupe_against_existing(facts, existing)

        added = 0
        for fact in kept:
            try:
                self._pinned.upsert_memory(  # type: ignore[attr-defined]
                    tenant_id=tenant_id,
                    project_id=project_id,
                    user_id=user_id,
                    key=fact.key,
                    value=fact.value,
                    scope="auto",
                    confidence=fact.confidence,
                    source_session_id=session_id,
                )
                added += 1
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning(
                    "PinnedMemoryAutoExtractor: failed to persist %s: %s",
                    fact.key,
                    exc,
                )

        logger.info(
            "PinnedMemoryAutoExtractor: user=%s raw=%d kept=%d dropped=%d added=%d",
            user_id, len(facts), len(kept), len(dropped), added,
        )
        return ExtractionResult(added=added, dropped=len(dropped), raw_count=len(facts))

    def _existing_keys(
        self, tenant_id: str, project_id: str, user_id: str
    ) -> list[tuple[str, str]]:
        """Return ``[(key, value), ...]`` for all active pinned memories
        belonging to this user. Used for dedupe."""
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT key, value FROM pinned_memories
                WHERE tenant_id = %s AND project_id = %s AND user_id = %s AND is_active = 1
                """,
                (tenant_id, project_id, user_id),
            ).fetchall()
        return [(r["key"], r["value"]) for r in rows]


class LocalLlamaCppFactExtractor:
    """Default LLM client: hits local llama.cpp on ``base_url`` (e.g. port 8081)
    using the OpenAI-compatible /chat/completions endpoint. Synchronous.

    The model must be capable of following JSON-only instructions; Gemma E2B
    Q4 or any 7B+ instruct model works. A small draft model (Qwen3-0.6B)
    generally does NOT have reliable JSON output for structured extraction.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8081/v1",
        model: str = "local-gemma4-e2b-q4-bg",
        timeout: float = 30.0,
        max_tokens: int = 512,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens

    def extract(self, messages: list[Message]) -> str:
        import httpx  # local import — keeps module importable without httpx at test time

        prompt_messages: list[dict] = [
            {"role": "system", "content": _PINNED_EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": "\n".join(f"{m.role}: {m.content}" for m in messages),
            },
        ]
        resp = httpx.post(
            f"{self._base_url}/chat/completions",
            json={
                "model": self._model,
                "messages": prompt_messages,
                "temperature": 0.2,
                "max_tokens": self._max_tokens,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

