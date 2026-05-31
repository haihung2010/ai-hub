import pytest
from app.services.ihi_rag_service import PatternMatcher, IHIragService

def test_pattern_match_exact():
    matcher = PatternMatcher()
    pattern = {"t_min": 90, "t_max": 100, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65}
    reading = {"t": 95, "v": 3.0, "c": 50}
    assert matcher.matches(pattern, reading) == True

def test_pattern_match_partial():
    matcher = PatternMatcher()
    pattern = {"t_min": 85, "t_max": 100, "v_min": 5.0, "v_max": 8.0, "c_min": 0, "c_max": 65}
    reading = {"t": 87, "v": 5.5}  # c is None, should still match
    assert matcher.matches(pattern, reading) == True

def test_pattern_no_match():
    matcher = PatternMatcher()
    pattern = {"t_min": 90, "t_max": 100, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65}
    reading = {"t": 80, "v": 3.0, "c": 50}  # t below range
    assert matcher.matches(pattern, reading) == False

def test_symptom_classify_overheat():
    matcher = PatternMatcher()
    assert matcher.classify_symptom(temp=95, vib=None, curr=None) == "overheat"
    assert matcher.classify_symptom(temp=87, vib=5.0, curr=None) == "overheat_precursor"

def test_symptom_classify_vibration():
    matcher = PatternMatcher()
    assert matcher.classify_symptom(temp=80, vib=6.5, curr=None) == "excessive_vibration"
    assert matcher.classify_symptom(temp=80, vib=5.0, curr=None) == "vibration_precursor"

def test_symptom_classify_overload():
    matcher = PatternMatcher()
    assert matcher.classify_symptom(temp=80, vib=3.0, curr=80) == "overload"

def test_symptom_classify_combined():
    matcher = PatternMatcher()
    assert matcher.classify_symptom(temp=95, vib=5.5, curr=70) == "overheat_vibration"

def test_symptom_classify_multi():
    matcher = PatternMatcher()
    assert matcher.classify_symptom(temp=95, vib=6.5, curr=80) == "multi_param"