import pytest
from app.services.ihi_analyzer import IHIAnalyzer, AlertResult
from app.models.ihi import AlertLevel


def test_danger_temperature():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=95, vibration=5.2, current=68)
    assert result.alert == AlertLevel.DANGER
    assert "temperature > 90" in result.reason


def test_danger_vibration():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=80, vibration=6.5, current=60)
    assert result.alert == AlertLevel.DANGER
    assert "vibration > 6.0" in result.reason


def test_danger_current():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=75, vibration=3.0, current=80)
    assert result.alert == AlertLevel.DANGER
    assert "current > 75" in result.reason


def test_warning_temperature():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=87, vibration=3.0, current=60)
    assert result.alert == AlertLevel.WARNING
    assert "85 < temperature <= 90" in result.reason


def test_warning_vibration():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=80, vibration=5.0, current=60)
    assert result.alert == AlertLevel.WARNING
    assert "4.5 < vibration <= 6.0" in result.reason


def test_normal():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=45, vibration=1.5, current=35)
    assert result.alert == AlertLevel.NORMAL
    assert result.reason is None


def test_analyze_batch():
    analyzer = IHIAnalyzer()
    readings = [
        ("M-001", 95, 5.2, 82),   # DANGER
        ("M-002", 88, 4.8, 68),   # WARNING
        ("M-003", 45, 1.5, 35),   # NORMAL
    ]
    results = analyzer.analyze_batch(readings)
    danger = [r for r in results if r.alert == AlertLevel.DANGER]
    warning = [r for r in results if r.alert == AlertLevel.WARNING]
    normal = [r for r in results if r.alert == AlertLevel.NORMAL]
    assert len(danger) == 1
    assert len(warning) == 1
    assert len(normal) == 1