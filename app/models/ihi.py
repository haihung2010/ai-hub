from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    DANGER = "DANGER"
    WARNING = "WARNING"
    NORMAL = "NORMAL"


class SeverityLevel(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class SensorReading(BaseModel):
    id: str = Field(..., description="Device ID short code")
    t: float = Field(..., description="Temperature in °C")
    v: float = Field(..., description="Vibration in mm/s")
    c: float = Field(..., description="Current in A")


class SensorDataRequest(BaseModel):
    ts: str = Field(..., description="Timestamp in DD/MM HH:MM format")
    data: List[SensorReading] = Field(default_factory=list)


class PatternRange(BaseModel):
    """Numeric range pattern for matching sensor readings.

    Backward-compat: original 3-parameter t/v/c pattern unchanged.
    Extension: `extra` allows arbitrary measurement thresholds for
    new sensor types (battery_pct, v_imbalance_pct, AI1_voltage, etc.)
    """
    t_min: float = Field(default=0.0, description="Min temperature °C")
    t_max: float = Field(default=0.0, description="Max temperature °C")
    v_min: float = Field(default=0.0, description="Min vibration mm/s")
    v_max: float = Field(default=0.0, description="Max vibration mm/s")
    c_min: float = Field(default=0.0, description="Min current A")
    c_max: float = Field(default=0.0, description="Max current A")
    extra: dict[str, dict] = Field(
        default_factory=dict,
        description="Extended thresholds: {measurement: {min_value, max_value, severity}}",
    )


class RAGCase(BaseModel):
    case_id: str
    severity: SeverityLevel
    symptom: str
    pattern: PatternRange
    description: str
    resolution: Optional[str] = None
    status: str = "active"
    match_count: int = 0


class RAGCreateRequest(BaseModel):
    case_id: str
    severity: SeverityLevel
    symptom: str
    pattern: PatternRange
    description: str
    resolution: Optional[str] = None
    status: str = "active"


class FeedbackRequest(BaseModel):
    case_id: str
    feedback: str
    rating: Optional[int] = None


class FeedbackCreateRequest(BaseModel):
    ts: str
    device_id: str
    severity: SeverityLevel
    description: str
    resolution: Optional[str] = None


class RAGResponse(BaseModel):
    case_id: str
    status: str
    pattern: PatternRange


class AnalyzeResponse(BaseModel):
    alert: AlertLevel
    devices: List[str]
    case_id: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    symptom: Optional[str] = None


# === IHI evaluate (LLM-based, independent of IHI rule) ===

class DeviceReadings(BaseModel):
    """All measurements for a single device at one snapshot moment."""
    type: str = Field(default="", description="Profile name e.g. MQTT-Vibration-Sensor")
    readings: dict = Field(default_factory=dict, description="measurement_name -> value")


class IHIEvaluateRequest(BaseModel):
    phase: int = Field(..., description="1 or 2 — independent of each other", ge=1, le=2)
    sample_time: str = Field(..., description="ISO timestamp the snapshot was taken")
    devices: dict = Field(..., description="device_name -> DeviceReadings")


class IHIEvaluateResponse(BaseModel):
    phase: int
    sample_time: str
    verdict_text: str = Field(..., description="Raw LLM analysis text")
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0