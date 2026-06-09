"""Save LLM verdicts as RAG cases for future retrieval (auto-learn).

Skip NORMAL verdicts (high volume, low signal) and low-confidence verdicts.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.models.ihi import AlertLevel, AnalyzeResponse
from app.services.ihi_rag_service import IHIragService

logger = logging.getLogger(__name__)


class IHICaseSaver:
    """Save LLM verdicts as RAG cases."""

    def __init__(self, db_pool):
        self.db_pool = db_pool
        self.rag_service = IHIragService(db_pool=db_pool)
        self.rag_service.load_cases()

    def save_verdict(
        self,
        scrape_id: int,
        phase: int,
        sample_time: str,
        readings: dict,
        llm_result: AnalyzeResponse,
    ) -> Optional[int]:
        """Save LLM verdict as new RAG case. Returns case_id or None if skipped."""
        # Skip NORMAL (too noisy)
        if llm_result.alert == AlertLevel.NORMAL:
            return None
        # Skip low confidence
        if llm_result.confidence < 0.5:
            return None

        # Build pattern from readings.
        # TODO: derive from device specs — for now use a sane industrial-temp default
        # (70-90 °C) instead of the prior 0-100 range which matched every reading.
        pattern = {
            "t_min": 70, "t_max": 90, "v_min": 0, "v_max": 10,
            "c_min": 0, "c_max": 100,
            "extra": {},
        }
        for m in ("battery_pct", "v_imbalance_pct", "AI1_voltage", "f_hz", "power_factor"):
            v = readings.get(m)
            if v is not None:
                if m in ("battery_pct", "f_hz"):
                    # Min-based: store as min_value (v - 5%)
                    pattern["extra"][m] = {
                        "min_value": max(0, v * 0.95),
                        "max_value": v * 1.05,
                    }
                else:
                    # Max-based
                    pattern["extra"][m] = {
                        "min_value": v * 0.95,
                        "max_value": v * 1.05,
                    }

        # Save to PG
        try:
            device_id = f"scrape_{scrape_id}_p{phase}"
            case_id = self.rag_service.create_case(
                device_id=device_id,
                severity=llm_result.alert.value.lower(),
                pattern=pattern,
                description=llm_result.narrative or "(auto from LLM verdict)",
                confirmed_by="auto_learned",
            )
            # Update vector index in memory (best effort)
            try:
                from app.services.vector_index import IHIVectorIndex
                vec_idx = IHIVectorIndex()
                emb = vec_idx.embed(llm_result.narrative or device_id)
                with self.db_pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO ihi_case_embeddings (case_id, embedding)
                            VALUES (%s, %s::vector)
                            ON CONFLICT (case_id) DO UPDATE SET embedding = EXCLUDED.embedding
                        """, (case_id, str(emb)))
                    conn.commit()
            except Exception as e:
                logger.warning("Vector embedding save failed for case %s: %s", case_id, e)
            return case_id
        except Exception as e:
            logger.warning("Case save failed: %s", e)
            return None
