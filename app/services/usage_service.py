"""Persist per-request usage/latency/cost events for admin observability."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.core.database import get_db_connection


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return round(ordered[index], 3)


@dataclass(frozen=True)
class UsageEvent:
    tenant_id: str
    project_id: str
    provider: str
    model: str
    latency_ms: float
    status_code: int
    session_id: str | None = None
    api_key_id: str | None = None
    user_id: str | None = None
    route_alias: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    error_type: str | None = None
    fallback_used: bool = False
    queue_wait_ms: float | None = None
    route_reason: str | None = None


class UsageService:
    def record(self, event: UsageEvent) -> str:
        event_id = f"usage_{uuid4().hex}"
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO usage_events (
                    id, tenant_id, api_key_id, user_id, project_id, session_id,
                    provider, model, route_alias, prompt_tokens, completion_tokens,
                    total_tokens, cost_usd, latency_ms, status_code, error_type,
                    fallback_used, queue_wait_ms, route_reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event_id,
                    event.tenant_id,
                    event.api_key_id,
                    event.user_id,
                    event.project_id,
                    event.session_id,
                    event.provider,
                    event.model,
                    event.route_alias,
                    event.prompt_tokens,
                    event.completion_tokens,
                    event.total_tokens,
                    event.cost_usd,
                    event.latency_ms,
                    event.status_code,
                    event.error_type,
                    int(event.fallback_used),
                    event.queue_wait_ms,
                    event.route_reason,
                ),
            )
            conn.commit()
        return event_id

    def get_time_series(self, days: int = 1, bucket: str = "hour") -> list[dict[str, object]]:
        with get_db_connection() as conn:
            # interval conversion for safety
            interval = f"{days} days"
            rows = conn.execute(
                f"""
                SELECT
                    DATE_TRUNC(%s, created_at) AS bucket,
                    COUNT(*) AS total_requests,
                    SUM(CASE WHEN status_code >= 200 AND status_code < 400 THEN 1 ELSE 0 END) AS success_requests,
                    SUM(CASE WHEN status_code >= 400 OR error_type IS NOT NULL THEN 1 ELSE 0 END) AS error_requests,
                    AVG(latency_ms) AS avg_latency_ms
                FROM usage_events
                WHERE created_at > NOW() - %s::interval
                GROUP BY 1
                ORDER BY 1 ASC
                """,
                (bucket, interval),
            ).fetchall()
        return [
            {
                "bucket": row["bucket"].isoformat() if row["bucket"] else None,
                "total_requests": row["total_requests"],
                "success_requests": row["success_requests"] or 0,
                "error_requests": row["error_requests"] or 0,
                "avg_latency_ms": round(row["avg_latency_ms"] or 0, 3),
            }
            for row in rows
        ]

    def get_cost_series_7d(self) -> list[dict[str, object]]:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    DATE_TRUNC('day', created_at) AS day,
                    COALESCE(SUM(cost_usd), 0) AS cost_usd
                FROM usage_events
                WHERE created_at > NOW() - INTERVAL '7 days'
                GROUP BY 1
                ORDER BY 1 ASC
                """
            ).fetchall()
        return [
            {
                "day": row["day"].strftime("%m/%d") if row["day"] else None,
                "cost_usd": round(float(row["cost_usd"] or 0), 6),
            }
            for row in rows
        ]

    def summary(self) -> dict[str, object]:
        time_series = self.get_time_series()
        cost_series_7d = self.get_cost_series_7d()
        with get_db_connection() as conn:
            totals = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_requests,
                    SUM(CASE WHEN status_code >= 200 AND status_code < 400 THEN 1 ELSE 0 END) AS success_requests,
                    SUM(CASE WHEN status_code >= 400 OR error_type IS NOT NULL THEN 1 ELSE 0 END) AS error_requests,
                    SUM(fallback_used) AS fallback_requests,
                    AVG(latency_ms) AS avg_latency_ms,
                    MAX(latency_ms) AS max_latency_ms,
                    AVG(queue_wait_ms) AS avg_queue_wait_ms,
                    MAX(queue_wait_ms) AS max_queue_wait_ms,
                    COUNT(queue_wait_ms) AS queue_wait_requests,
                    COALESCE(SUM(cost_usd), 0) AS total_cost_usd,
                    COALESCE(SUM(CASE WHEN DATE_TRUNC('month', created_at) = DATE_TRUNC('month', CURRENT_DATE) THEN cost_usd ELSE 0 END), 0) AS month_cost_usd
                FROM usage_events
                """
            ).fetchone()
            latency_rows = conn.execute(
                "SELECT latency_ms FROM usage_events WHERE latency_ms IS NOT NULL ORDER BY created_at DESC LIMIT 1000"
            ).fetchall()
            by_provider_rows = conn.execute(
                "SELECT provider, COUNT(*) AS requests, AVG(latency_ms) AS avg_latency_ms "
                "FROM usage_events GROUP BY provider ORDER BY requests DESC"
            ).fetchall()
            by_model_rows = conn.execute(
                "SELECT model, COUNT(*) AS requests, AVG(latency_ms) AS avg_latency_ms "
                "FROM usage_events GROUP BY model ORDER BY requests DESC"
            ).fetchall()
            by_route_rows = conn.execute(
                "SELECT COALESCE(route_alias, 'unknown') AS route_alias, COUNT(*) AS requests, "
                "SUM(fallback_used) AS fallback_requests, AVG(latency_ms) AS avg_latency_ms "
                "FROM usage_events GROUP BY COALESCE(route_alias, 'unknown') ORDER BY requests DESC"
            ).fetchall()
            by_route_reason_rows = conn.execute(
                "SELECT COALESCE(route_reason, 'unknown') AS route_reason, COUNT(*) AS requests, "
                "AVG(queue_wait_ms) AS avg_queue_wait_ms, SUM(fallback_used) AS fallback_requests "
                "FROM usage_events GROUP BY COALESCE(route_reason, 'unknown') ORDER BY requests DESC"
            ).fetchall()
            by_project_rows = conn.execute(
                "SELECT tenant_id, project_id, COUNT(*) AS requests, AVG(latency_ms) AS avg_latency_ms, "
                "SUM(fallback_used) AS fallback_requests "
                "FROM usage_events GROUP BY tenant_id, project_id ORDER BY requests DESC LIMIT 20"
            ).fetchall()
            by_status_rows = conn.execute(
                "SELECT status_code, COALESCE(error_type, '') AS error_type, COUNT(*) AS requests "
                "FROM usage_events GROUP BY status_code, COALESCE(error_type, '') ORDER BY requests DESC"
            ).fetchall()
            recent_rows = conn.execute(
                "SELECT tenant_id, project_id, provider, model, route_alias, latency_ms, "
                "status_code, error_type, fallback_used, queue_wait_ms, route_reason, created_at "
                "FROM usage_events ORDER BY created_at DESC LIMIT 50"
            ).fetchall()

        total_requests = totals["total_requests"] or 0
        fallback_requests = totals["fallback_requests"] or 0
        latencies = [row["latency_ms"] for row in latency_rows if row["latency_ms"] is not None]
        return {
            "total_requests": total_requests,
            "total_cost_usd": round(float(totals["total_cost_usd"] or 0), 6),
            "month_cost_usd": round(float(totals["month_cost_usd"] or 0), 6),
            "success_requests": totals["success_requests"] or 0,
            "error_requests": totals["error_requests"] or 0,
            "fallback_requests": fallback_requests,
            "fallback_rate": round(fallback_requests / total_requests, 4) if total_requests else 0.0,
            "latency": {
                "avg_ms": round(totals["avg_latency_ms"] or 0, 3),
                "max_ms": round(totals["max_latency_ms"] or 0, 3),
                "p50_ms": _percentile(latencies, 0.50),
                "p95_ms": _percentile(latencies, 0.95),
                "p99_ms": _percentile(latencies, 0.99),
            },
            "queue_wait": {
                "requests": totals["queue_wait_requests"] or 0,
                "avg_ms": round(totals["avg_queue_wait_ms"] or 0, 3),
                "max_ms": round(totals["max_queue_wait_ms"] or 0, 3),
            },
            "by_provider": [
                {
                    "provider": row["provider"],
                    "requests": row["requests"],
                    "avg_latency_ms": round(row["avg_latency_ms"] or 0, 3),
                }
                for row in by_provider_rows
            ],
            "by_model": [
                {
                    "model": row["model"],
                    "requests": row["requests"],
                    "avg_latency_ms": round(row["avg_latency_ms"] or 0, 3),
                }
                for row in by_model_rows
            ],
            "by_route": [
                {
                    "route_alias": row["route_alias"],
                    "requests": row["requests"],
                    "fallback_requests": row["fallback_requests"] or 0,
                    "avg_latency_ms": round(row["avg_latency_ms"] or 0, 3),
                }
                for row in by_route_rows
            ],
            "by_route_reason": [
                {
                    "route_reason": row["route_reason"],
                    "requests": row["requests"],
                    "avg_queue_wait_ms": round(row["avg_queue_wait_ms"] or 0, 3),
                    "fallback_requests": row["fallback_requests"] or 0,
                }
                for row in by_route_reason_rows
            ],
            "by_project": [
                {
                    "tenant_id": row["tenant_id"],
                    "project_id": row["project_id"],
                    "requests": row["requests"],
                    "avg_latency_ms": round(row["avg_latency_ms"] or 0, 3),
                    "fallback_requests": row["fallback_requests"] or 0,
                }
                for row in by_project_rows
            ],
            "by_status": [
                {
                    "status_code": row["status_code"],
                    "error_type": row["error_type"] or None,
                    "requests": row["requests"],
                }
                for row in by_status_rows
            ],
            "recent": [dict(row) for row in recent_rows],
            "time_series": time_series,
            "cost_series_7d": cost_series_7d,
        }
