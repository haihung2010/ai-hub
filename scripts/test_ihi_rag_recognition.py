"""
Test IHI RAG re-recognition: readings return NORMAL -> create RAG case -> same readings return WARNING.
Run: python scripts/test_ihi_rag_recognition.py
"""
import asyncio
import httpx

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL = "http://localhost:8000"
TIMEOUT = 30.0

# Mixed sensors: 7 boundary (NORMAL by rules, NOT matched by any existing RAG case), 3 normal
# Existing RAG cases that interfere:
#   case5:  t=[85,90], v=[0,4.5],  c=[0,65]   -> catches t>=85
#   case 9:  t=[80,85], v=[3,4.5],  c=[55,65]  -> catches t<=85 AND v>=3 AND c>=55
# Boundary sensors must be: t<84 (below case5 t_min AND below case9 t_max),
#   v<3 (below case9 v_min), c<55 (below case9 c_min) to avoid all existing cases.
# Rule thresholds: WARNING if 85 < temp <= 90 OR 4.5 < vib <= 6.0 OR 65 < current <= 75
# So t=84.9, v=2.0, c=50.0 is NORMAL by rules AND no existing case matches.
BOUNDARY_SENSORS = [
    {"id": "M-001", "t": 84.9, "v": 2.0, "c": 50.0},
    {"id": "M-002", "t": 84.9, "v": 2.0, "c": 50.0},
    {"id": "M-003", "t": 84.9, "v": 2.0, "c": 50.0},
    {"id": "M-004", "t": 84.9, "v": 2.0, "c": 50.0},
    {"id": "M-005", "t": 84.9, "v": 2.0, "c": 50.0},
    {"id": "M-006", "t": 84.9, "v": 2.0, "c": 50.0},
    {"id": "M-007", "t": 84.9, "v": 2.0, "c": 50.0},
]
NORMAL_SENSORS = [
    {"id": "M-008", "t": 45.0, "v": 1.5, "c": 35.0},
    {"id": "M-009", "t": 42.0, "v": 1.2, "c": 32.0},
    {"id": "M-010", "t": 48.0, "v": 1.8, "c": 38.0},
]
ALL_SENSORS = BOUNDARY_SENSORS + NORMAL_SENSORS


async def analyze(client, sensors, label=""):
    resp = await client.post(
        f"{BASE_URL}/v1/ihi/analyze",
        headers={"X-API-KEY": API_KEY},
        json={"ts": "31/05 15:00", "data": sensors}
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"  [{label}] alert={result['alert']}, devices={result.get('devices', [])}, case_id={result.get('case_id')}, confidence={result.get('confidence', 0)}")
    return result


async def cleanup_boundary_cases(client):
    """Delete any existing BOUNDARY-WARN-01 cases to ensure clean test state.

    The API returns case_id as the DB numeric id (e.g. "19").
    We need to find the case whose pattern matches our test pattern.
    """
    resp = await client.get(f"{BASE_URL}/v1/ihi/rag", headers={"X-API-KEY": API_KEY})
    resp.raise_for_status()
    cases = resp.json()
    for case in cases:
        pat = case.get("pattern", {})
        if (pat.get("t_min") == 84.0 and pat.get("t_max") == 90.0
                and pat.get("v_min") == 1.0 and pat.get("v_max") == 5.0
                and pat.get("c_min") == 45.0 and pat.get("c_max") == 65.0):
            await client.delete(f"{BASE_URL}/v1/ihi/rag/{case['case_id']}", headers={"X-API-KEY": API_KEY})
            print(f"  [CLEANUP] deleted case_id={case['case_id']}")


async def create_rag_case(client):
    resp = await client.post(
        f"{BASE_URL}/v1/ihi/rag",
        headers={"X-API-KEY": API_KEY},
        json={
            "case_id": "BOUNDARY-WARN-01",
            "severity": "CRITICAL",
            "symptom": "overheat_precursor",
            "pattern": {"t_min": 84.0, "t_max": 90.0, "v_min": 1.0, "v_max": 5.0, "c_min": 45.0, "c_max": 65.0},
            "description": "Motor running hot — boundary temperature with elevated vibration",
            "resolution": None,
            "status": "active"
        }
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"  [RAG CREATED] case_id={result.get('case_id')}, severity={result.get('severity')}")
    return result


async def main():
    print("=" * 60)
    print("IHI RAG Re-Recognition Test")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # Cleanup any existing test cases first
        print("\n[CLEANUP] Removing any existing BOUNDARY-WARN-01 cases...")
        await cleanup_boundary_cases(client)

        # Step 1: baseline — all sensors return NORMAL
        print("\n[STEP 1] Sending mixed sensors (baseline — expect NORMAL)...")
        result1 = await analyze(client, ALL_SENSORS, "STEP1")
        step1_pass = result1["alert"] == "NORMAL" and len(result1.get("devices", [])) == 0
        print(f"  -> {'PASS' if step1_pass else 'FAIL'}: alert={result1['alert']}")

        # Step 2: create RAG case
        print("\n[STEP 2] Manager creates RAG case (overheat_precursor pattern)...")
        await create_rag_case(client)

        # Step 3: re-analyze — boundary sensors should now return WARNING
        print("\n[STEP 3] Re-sending same sensors (expect WARNING for boundary)...")
        result3 = await analyze(client, ALL_SENSORS, "STEP3")

        boundary_ids = [s["id"] for s in BOUNDARY_SENSORS]
        normal_ids = [s["id"] for s in NORMAL_SENSORS]

        step3_alert_pass = result3["alert"] == "DANGER"
        step3_case_pass = result3.get("case_id") is not None
        step3_conf_pass = result3.get("confidence", 0) > 0.5
        # RAG-only match: alert upgraded but devices list only reflects rule-based results
        # (empty since boundary sensors are NORMAL by rules)
        step3_devices_empty = len(result3.get("devices", [])) == 0

        print(f"\n  alert is DANGER: {'PASS' if step3_alert_pass else 'FAIL'}")
        print(f"  case_id returned: {'PASS' if step3_case_pass else 'FAIL'}")
        print(f"  confidence > 0.5: {'PASS' if step3_conf_pass else 'FAIL'}")
        print(f"  devices list empty (RAG-only, no rule match): {'PASS' if step3_devices_empty else 'FAIL'}")

        all_pass = step1_pass and step3_alert_pass and step3_case_pass and step3_conf_pass and step3_devices_empty
        print(f"\n{'='*60}")
        print(f"OVERALL: {'ALL PASS' if all_pass else 'SOME FAILURES'}")
        print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
