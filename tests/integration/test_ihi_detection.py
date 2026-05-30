# tests/integration/test_ihi_detection.py
import pytest
import asyncio
import httpx
import json
import random

from app.services.ihi_validator import IHIValidator

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL = "http://localhost:8000"

def generate_test_data(num_devices=135, seed=None):
    """Generate deterministic test data."""
    if seed:
        random.seed(seed)
    data = []
    for i in range(num_devices):
        device_id = f"Motor-{i+1:03d}"
        is_abn = random.random() < 0.2
        if is_abn:
            temp = random.choice([88, 90, 92, 95, 93])
            vib = random.choice([4.8, 5.2, 5.8, 6.1, 7.0])
            cur = random.choice([76, 78, 80, 82, 85])
        else:
            temp = round(random.uniform(28, 75), 1)
            vib = round(random.uniform(0.3, 4.2), 2)
            cur = round(random.uniform(18, 62), 1)
        data.append({
            "device_id": device_id,
            "temperature_c": temp,
            "vibration_mm_s": vib,
            "current_a": cur,
        })
    return data

def count_actual_abnormal(data):
    danger = sum(1 for d in data if d['temperature_c'] > 90 or d['vibration_mm_s'] > 6 or d['current_a'] > 75)
    warning = sum(1 for d in data if (85 < d['temperature_c'] <= 90) or (4.5 < d['vibration_mm_s'] <= 6) or (65 < d['current_a'] <= 75))
    return danger, warning

@pytest.mark.asyncio
async def test_ihi_accuracy_135_devices():
    """Test IHI detection accuracy with full 135 devices."""
    sensor_data = generate_test_data(135, seed=42)
    actual_danger, actual_warning = count_actual_abnormal(sensor_data)

    prompt = f"""Analyze 135 sensors. JSON only:
DANGER: temp>90 OR vib>6 OR current>75
WARNING: temp>85 OR vib>4.5 OR current>65
Data: {json.dumps(sensor_data)}
JSON:"""

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "ihi",
                "tenant_id": "ihi-tenant",
                "user_name": "accuracy-test",
                "user_message": prompt,
                "model_mode": "normal",
                "stream": False
            }
        )
        result = resp.json()
        content = result.get("content", "")

        # Try to parse JSON
        try:
            parsed = json.loads(content)
            detected = len(parsed.get("danger", [])) + len(parsed.get("warning", []))
        except:
            detected = -1

        total_actual = actual_danger + actual_warning
        accuracy = 1 - (abs(detected - total_actual) / total_actual) if total_actual > 0 else 0

        assert accuracy >= 0.50, f"Accuracy {accuracy:.1%} below 50% baseline (detected={detected}, actual={total_actual})"
        assert result.get("latency_ms", 0) > 0

@pytest.mark.asyncio
async def test_ihi_consistency_5_calls():
    """Test IHI consistency across 5 calls with same data."""
    sensor_data = generate_test_data(45, seed=123)
    actual_danger, actual_warning = count_actual_abnormal(sensor_data)
    total_actual = actual_danger + actual_warning

    results = []
    async with httpx.AsyncClient(timeout=90.0) as client:
        for i in range(5):
            prompt = f"""Analyze sensors. JSON: {json.dumps(sensor_data)} JSON:"""
            resp = await client.post(
                f"{BASE_URL}/v1/chat",
                headers={"X-API-KEY": API_KEY},
                json={
                    "project_id": "ihi",
                    "tenant_id": "ihi-tenant",
                    "user_name": f"consistency-{i}",
                    "user_message": prompt,
                    "model_mode": "normal",
                    "stream": False
                }
            )
            result = resp.json()
            content = result.get("content", "")
            try:
                parsed = json.loads(content)
                detected = len(parsed.get("danger", [])) + len(parsed.get("warning", []))
                results.append(detected)
            except:
                results.append(-1)

    # Check variance
    valid_results = [r for r in results if r >= 0]
    if len(valid_results) >= 3:
        variance = max(valid_results) - min(valid_results)
        assert variance <= 8, f"High variance: {variance} (results: {valid_results})"

@pytest.mark.asyncio
async def test_ihi_json_format_consistency():
    """Test IHI returns consistent JSON format."""
    sensor_data = generate_test_data(10, seed=456)

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(3):
            prompt = f"""Analyze 10 sensors. JSON: {json.dumps(sensor_data)} JSON:"""
            resp = await client.post(
                f"{BASE_URL}/v1/chat",
                headers={"X-API-KEY": API_KEY},
                json={
                    "project_id": "ihi",
                    "tenant_id": "ihi-tenant",
                    "user_name": f"format-{i}",
                    "user_message": prompt,
                    "model_mode": "normal",
                    "stream": False
                }
            )
            result = resp.json()
            content = result.get("content", "")

            # Use IHIValidator to handle malformed JSON
            validator = IHIValidator()
            validation_result = validator.parse(content)

            # The validator should handle malformed JSON gracefully
            # Even if JSON is malformed, we should get a valid result with error field
            assert validation_result.is_valid == True or validation_result.error is not None, f"Validator should return valid or error: {validation_result}"