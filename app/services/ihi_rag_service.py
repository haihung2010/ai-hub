"""
IHI RAG Service - Industrial IoT RAG Case Management

Provides pattern matching and case retrieval for industrial equipment monitoring.
"""

import json
from typing import Optional


SYMPTOM_TAXONOMY = {
    "overheat": ["overheat", "overheat_vibration", "overheat_overload"],
    "vibration": ["excessive_vibration", "vibration_precursor", "overheat_vibration"],
    "current": ["overload", "overload_precursor", "vibration_overload"],
    "multi": ["multi_param", "overheat_vibration", "vibration_overload", "overheat_overload"]
}


class PatternMatcher:
    """Pattern matching for IHI sensor readings."""

    def matches(self, pattern: dict, reading: dict) -> bool:
        """Check if reading matches pattern (including extra thresholds)."""
        # Check temperature
        if reading.get("t") is not None:
            t = reading["t"]
            if "t_min" in pattern and t < pattern["t_min"]:
                return False
            if "t_max" in pattern and t > pattern["t_max"]:
                return False
        # Check vibration
        if reading.get("v") is not None:
            v = reading["v"]
            if "v_min" in pattern and v < pattern["v_min"]:
                return False
            if "v_max" in pattern and v > pattern["v_max"]:
                return False
        # Check current
        if reading.get("c") is not None:
            c = reading["c"]
            if "c_min" in pattern and c < pattern["c_min"]:
                return False
            if "c_max" in pattern and c > pattern["c_max"]:
                return False
        # Check extra thresholds (NEW)
        extra = pattern.get("extra", {})
        for measurement, bounds in extra.items():
            value = reading.get(measurement)
            if value is None:
                continue  # missing measurement doesn't disqualify
            if "min_value" in bounds and value < bounds["min_value"]:
                return False
            if "max_value" in bounds and value > bounds["max_value"]:
                return False
        return True

    def classify_symptom(self, temp: Optional[float], vib: Optional[float], curr: Optional[float]) -> str:
        """
        Classify symptom based on sensor readings.

        Returns symptom name based on thresholds:
        - overheat: temp > 90
        - overheat_precursor: 85 < temp <= 90
        - excessive_vibration: vib > 6.0
        - vibration_precursor: 4.5 < vib <= 6.0
        - overload: curr > 75
        - overload_precursor: 65 < curr <= 75
        - combined: 2+ params anomalous (one full + one or more precursor)
        - multi_param: all 3 params at full anomaly level
        """
        # Determine anomaly levels
        is_overheat = temp is not None and temp > 90
        is_overheat_precursor = temp is not None and 85 < temp <= 90

        is_excessive_vibration = vib is not None and vib > 6.0
        is_vibration_precursor = vib is not None and 4.5 < vib <= 6.0

        is_overload = curr is not None and curr > 75
        is_overload_precursor = curr is not None and 65 < curr <= 75

        # Multi-param: all 3 params at full anomaly level
        if is_overheat and is_excessive_vibration and is_overload:
            return "multi_param"

        # Combined symptoms (one FULL anomaly + one PRECURSOR anomaly)
        # overheat_vibration: temp > 90 AND vibration_precursor (4.5 < vib <= 6.0)
        if is_overheat and is_vibration_precursor:
            return "overheat_vibration"

        # overheat_overload: temp > 90 AND overload_precursor (65 < curr <= 75)
        if is_overheat and is_overload_precursor:
            return "overheat_overload"

        # vibration_overload: excessive_vibration (vib > 6.0) AND any current issue
        if is_excessive_vibration and (is_overload or is_overload_precursor):
            return "vibration_overload"

        # Single anomalies
        if is_overheat:
            return "overheat"
        if is_overheat_precursor:
            return "overheat_precursor"
        if is_excessive_vibration:
            return "excessive_vibration"
        if is_vibration_precursor:
            return "vibration_precursor"
        if is_overload:
            return "overload"
        if is_overload_precursor:
            return "overload_precursor"

        return "normal"


class IHIragService:
    """RAG service for IHI case management."""

    def __init__(self, db_pool=None):
        """
        Initialize IHI RAG service.

        Args:
            db_pool: Optional database connection pool
        """
        self._db_pool = db_pool
        self._case_cache = []
        self._case_map = {}
        self.matcher = PatternMatcher()

    def load_cases(self) -> int:
        """
        Load RAG cases from database into cache.

        Returns:
            Number of cases loaded
        """
        if self._db_pool is None:
            return 0

        try:
            with self._db_pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, device_id, severity, symptom, pattern, description,
                               confirmed_by, match_count, created_at
                        FROM ihi_rag_cases
                        ORDER BY severity DESC, match_count DESC
                    """)
                    rows = cur.fetchall()

            self._case_cache = []
            self._case_map = {}

            for row in rows:
                case = {
                    "id": row["id"],
                    "device_id": row["device_id"],
                    "severity": row["severity"],
                    "symptom": row.get("symptom", ""),
                    "pattern": row["pattern"] if isinstance(row["pattern"], dict) else {},
                    "description": row["description"],
                    "confirmed_by": row["confirmed_by"],
                    "match_count": row["match_count"] or 0,
                    "created_at": row["created_at"]
                }
                self._case_cache.append(case)
                self._case_map[row["id"]] = case

            return len(self._case_cache)
        except Exception:
            return 0

    def find_matching_case(self, temp: Optional[float], vib: Optional[float],
                          curr: Optional[float]) -> tuple:
        """
        Find RAG case matching sensor reading.

        Args:
            temp: Temperature reading
            vib: Vibration reading
            curr: Current reading

        Returns:
            (case, confidence) or (None, 0) if no match
        """
        matcher = PatternMatcher()
        reading = {"t": temp, "v": vib, "c": curr}

        # First try exact severity match based on symptom
        symptom = matcher.classify_symptom(temp, vib, curr)

        # Score all cached cases
        best_case = None
        best_score = 0

        for case in self._case_cache:
            pattern = case.get("pattern", {})
            if not pattern:
                continue

            if matcher.matches(pattern, reading):
                # Calculate confidence based on match quality
                score = self._calculate_confidence(pattern, reading, symptom, case)
                if score > best_score:
                    best_score = score
                    best_case = case

        if best_case:
            return (best_case, best_score)
        return (None, 0)

    def _calculate_confidence(self, pattern: dict, reading: dict,
                               symptom: str, case: dict) -> float:
        """Calculate confidence score for a match."""
        confidence = 0.5  # Base confidence

        # Boost for severity
        severity = case.get("severity", "medium")
        if severity == "critical":
            confidence += 0.3
        elif severity == "high":
            confidence += 0.2
        elif severity == "medium":
            confidence += 0.1

        # Boost for match count (well-validated case)
        match_count = case.get("match_count", 0)
        if match_count > 10:
            confidence += 0.15
        elif match_count > 5:
            confidence += 0.1
        elif match_count > 0:
            confidence += 0.05

        # Check symptom alignment
        case_symptom = case.get("symptom", "")
        if case_symptom == symptom:
            confidence += 0.1

        return min(confidence, 1.0)

    def create_case(self, device_id: str, severity: str, pattern: dict,
                    description: str, confirmed_by: str) -> int:
        """
        Create new RAG case.

        Args:
            device_id: Device identifier
            severity: Case severity (low, medium, high, critical)
            pattern: Pattern dict with t_min, t_max, v_min, v_max, c_min, c_max
            description: Case description
            confirmed_by: Who confirmed this case

        Returns:
            case_id of created case
        """
        if self._db_pool is None:
            # In-memory only
            case_id = len(self._case_cache) + 1
            case = {
                "id": case_id,
                "device_id": device_id,
                "severity": severity,
                "pattern": pattern,
                "description": description,
                "confirmed_by": confirmed_by,
                "match_count": 0
            }
            self._case_cache.append(case)
            self._case_map[case_id] = case
            return case_id

        with self._db_pool.connection() as conn:
            with conn.cursor() as cur:
                # Classify symptom from pattern
                symptom = self.matcher.classify_symptom(
                    pattern.get("t_min"), pattern.get("v_min"), pattern.get("c_min")
                )
                cur.execute("""
                    INSERT INTO ihi_rag_cases (device_id, severity, symptom, pattern, description, confirmed_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (device_id, severity, symptom, json.dumps(pattern), description, confirmed_by))
                case_id = cur.fetchone()["id"]

        return case_id

    def get_case(self, case_id: int) -> Optional[dict]:
        """Get case by ID."""
        if case_id in self._case_map:
            return self._case_map[case_id]

        if self._db_pool is None:
            return None

        try:
            with self._db_pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, device_id, severity, symptom, pattern, description,
                               confirmed_by, match_count, created_at
                        FROM ihi_rag_cases
                        WHERE id = %s
                    """, (case_id,))
                    row = cur.fetchone()

            if row:
                return {
                    "id": row["id"],
                    "device_id": row["device_id"],
                    "severity": row["severity"],
                    "symptom": row.get("symptom", ""),
                    "pattern": row["pattern"] if isinstance(row["pattern"], dict) else {},
                    "description": row["description"],
                    "confirmed_by": row["confirmed_by"],
                    "match_count": row["match_count"] or 0,
                    "created_at": row["created_at"]
                }
        except Exception:
            pass

        return None

    def list_cases(self, severity: Optional[str] = None, limit: int = 100) -> list:
        """
        List cases with optional severity filter.

        Args:
            severity: Optional severity filter
            limit: Maximum number of cases to return

        Returns:
            List of cases
        """
        if self._case_cache and severity is None:
            return self._case_cache[:limit]

        if self._db_pool is None:
            if severity:
                return [c for c in self._case_cache if c.get("severity") == severity][:limit]
            return self._case_cache[:limit]

        try:
            with self._db_pool.connection() as conn:
                with conn.cursor() as cur:
                    if severity:
                        cur.execute("""
                            SELECT id, device_id, severity, symptom, pattern, description,
                                   confirmed_by, match_count, created_at
                            FROM ihi_rag_cases
                            WHERE severity = %s
                            ORDER BY severity DESC, match_count DESC
                            LIMIT %s
                        """, (severity, limit))
                    else:
                        cur.execute("""
                            SELECT id, device_id, severity, symptom, pattern, description,
                                   confirmed_by, match_count, created_at
                            FROM ihi_rag_cases
                            ORDER BY severity DESC, match_count DESC
                            LIMIT %s
                        """, (limit,))

                    rows = cur.fetchall()

            cases = []
            for row in rows:
                case = {
                    "id": row["id"],
                    "device_id": row["device_id"],
                    "severity": row["severity"],
                    "symptom": row.get("symptom", ""),
                    "pattern": row["pattern"] if isinstance(row["pattern"], dict) else {},
                    "description": row["description"],
                    "confirmed_by": row["confirmed_by"],
                    "match_count": row["match_count"] or 0,
                    "created_at": row["created_at"]
                }
                cases.append(case)

            return cases
        except Exception:
            return []

    def increment_match_count(self, case_id: int) -> bool:
        """
        Increment match count when case is used.

        Args:
            case_id: Case ID to increment

        Returns:
            True if successful, False otherwise
        """
        if case_id in self._case_map:
            self._case_map[case_id]["match_count"] = self._case_map[case_id].get("match_count", 0) + 1

        if self._db_pool is None:
            return True

        try:
            with self._db_pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE ihi_rag_cases
                        SET match_count = COALESCE(match_count, 0) + 1
                        WHERE id = %s
                    """, (case_id,))
            return True
        except Exception:
            return False