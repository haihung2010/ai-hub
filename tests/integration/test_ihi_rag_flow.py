# tests/integration/test_ihi_rag_flow.py
import pytest
import httpx

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL = "http://localhost:8000"


@pytest.mark.asyncio
async def test_analyze_danger():
    """Test DANGER alert for overheat."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/ihi/analyze",
            headers={"X-API-KEY": API_KEY},
            json={
                "ts": "29/05 14:35",
                "data": [{"id": "M-001", "t": 95, "v": 5.2, "c": 82}]
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["alert"] == "DANGER"
        assert "M-001" in result["devices"]


@pytest.mark.asyncio
async def test_analyze_warning():
    """Test WARNING alert for elevated readings."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/ihi/analyze",
            headers={"X-API-KEY": API_KEY},
            json={
                "ts": "29/05 14:35",
                "data": [{"id": "M-002", "t": 87, "v": 4.8, "c": 68}]
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["alert"] == "WARNING"
        assert "M-002" in result["devices"]


@pytest.mark.asyncio
async def test_analyze_normal():
    """Test NORMAL response for healthy readings."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/ihi/analyze",
            headers={"X-API-KEY": API_KEY},
            json={
                "ts": "29/05 14:35",
                "data": [{"id": "M-003", "t": 45, "v": 1.5, "c": 35}]
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["alert"] == "NORMAL"
        assert result["devices"] == []


@pytest.mark.asyncio
async def test_rag_list():
    """Test listing RAG cases."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{BASE_URL}/v1/ihi/rag",
            headers={"X-API-KEY": API_KEY}
        )
        assert resp.status_code == 200
        cases = resp.json()
        assert len(cases) >= 10  # At least our seed cases


@pytest.mark.asyncio
async def test_feedback_creates_rag():
    """Test that manager feedback creates new RAG entry via /v1/ihi/rag."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/ihi/rag",
            headers={"X-API-KEY": API_KEY},
            json={
                "case_id": "RAG-TEST01",
                "severity": "CRITICAL",
                "symptom": "overheat_vibration",
                "pattern": {"t_min": 90, "t_max": 100, "v_min": 5.0, "v_max": 8.0, "c_min": 70, "c_max": 85},
                "description": "Motor mới bị kêu lạ + nhiệt tăng",
                "resolution": "Đã kiểm tra, thay dầu bôi trơn"
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert "case_id" in result
        assert result["case_id"] is not None