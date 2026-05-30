# tests/unit/test_ihi_validator.py
import pytest
from app.services.ihi_validator import IHIValidator, ValidationResult

def test_parse_danger_warning_format():
    validator = IHIValidator()
    content = '{"danger":["Motor-001","Motor-002"],"warning":["Motor-003"],"normal_count":10}'
    result = validator.parse(content)
    assert result.danger == ["Motor-001", "Motor-002"]
    assert result.warning == ["Motor-003"]
    assert result.normal_count == 10
    assert result.is_valid == True

def test_parse_abnormal_format():
    validator = IHIValidator()
    content = '{"abnormal": [{"device_id": "Motor-001", "reason": "temp=95C"}]}'
    result = validator.parse(content)
    assert result.is_valid == True
    assert len(result.danger) >= 1

def test_reject_invalid_json():
    validator = IHIValidator()
    content = "This is not JSON"
    result = validator.parse(content)
    assert result.is_valid == False
    assert result.error is not None

def test_reject_empty_response():
    validator = IHIValidator()
    content = '{"danger":[],"warning":[],"normal_count":0}'
    result = validator.parse(content)
    # Empty response is valid but flagged
    assert result.is_valid == True
    assert result.is_empty == True