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
    t_min: float = Field(default=0.0, description="Min temperature °C")
    t_max: float = Field(default=0.0, description="Max temperature °C")
    v_min: float = Field(default=0.0, description="Min vibration mm/s")
    v_max: float = Field(default=0.0, description="Max vibration mm/s")
    c_min: float = Field(default=0.0, description="Min current A")
    c_max: float = Field(default=0.0, description="Max current A")


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


class AnalyzeResponse(BaseModel):
    alert: AlertLevel
    devices: List[str]
    case_id: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    symptom: Optional[str] = None