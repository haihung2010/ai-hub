import pytest
from app.models.ihi import (
    SensorReading,
    SensorDataRequest,
    AnalyzeResponse,
    RAGCase,
    RAGCreateRequest,
    FeedbackRequest,
    AlertLevel,
    SeverityLevel,
    PatternRange
)

def test_sensor_reading_model():
    reading = SensorReading(id="M-001", t=95.0, v=5.2, c=82.0)
    assert reading.id == "M-001"
    assert reading.t == 95.0

def test_sensor_data_request_parse():
    req = SensorDataRequest(
        ts="29/05 14:35",
        data=[
            {"id": "M-001", "t": 95, "v": 5.2, "c": 82},
            {"id": "M-002", "t": 88, "v": 4.8, "c": 68}
        ]
    )
    assert len(req.data) == 2
    assert req.data[0].t == 95

def test_analyze_response_format():
    resp = AnalyzeResponse(alert=AlertLevel.DANGER, devices=["M-001"], case_id=None, confidence=1.0)
    assert resp.alert == AlertLevel.DANGER
    assert resp.devices == ["M-001"]

def test_rag_case_model():
    case = RAGCase(
        case_id="RAG-001",
        severity=SeverityLevel.CRITICAL,
        symptom="overheat",
        pattern=PatternRange(t_min=90, t_max=100, v_min=0, v_max=4.5, c_min=0, c_max=65),
        description="Motor overheating",
        status="active"
    )
    assert case.severity == SeverityLevel.CRITICAL
    assert case.pattern.t_min == 90

def test_pattern_range():
    p = PatternRange(t_min=85, t_max=90, v_min=4.5, v_max=6.0, c_min=65, c_max=75)
    assert p.t_min == 85
    assert p.v_max == 6.0