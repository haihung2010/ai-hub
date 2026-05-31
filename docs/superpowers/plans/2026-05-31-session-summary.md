# Session Summary — 2026-05-31

## IHI RAG Re-Recognition Test
- **File:** `scripts/test_ihi_rag_recognition.py`
- **3-step flow:**
1. Send mixed sensors (t=84.9, v=2.0, c=50.0) → returns NORMAL (no existing RAG case matches)
  2. Manager creates CRITICAL RAG case via `POST /v1/ihi/rag` with pattern t=[84,90], v=[1,5], c=[45,65]
  3. Re-send same sensors → returns DANGER with case_id (RAG pattern match detected)
- **Result:** ✅ ALL PASS
- **Commit:** `45bd407`

## Fanpage Continuous Chat Test
- **File:** `scripts/test_fanpage_continuous.py`
- **100 calls** (20 cycles × 5 messages): greeting → product inquiry → price → thanks → new product
- **Metrics:** 0 errors, 0 empty responses, p95 latency 2491ms, 97/100 unique responses
- **Result:** ✅ ALL PASS
- **Commit:** `ca1839c`

## IHI AIHub Parallel Listener
- **File:** `scripts/ihi_aihub_listener.py`
- **Purpose:** Runs alongside PDM (existing system), reads InfluxDB sensor data, sends to AIHub `/v1/ihi/analyze`, logs structured results
- **Does NOT:** affect existing PDM system, send responses anywhere, replace PDM
- **Config:** reads from PDM `.env` (`/home/hung/hoang-project/project-ihi/AI/pdm_optimization/.env`)
- **Note:** Requires network access to InfluxDB (10.254.1.79:8086) — runs on server with InfluxDB access
- **Commit:** `02f3469`

## Admin Panel
- Seeded 7 tenants via `scripts/seed_multi_user.py` → admin panel at `http://localhost:8000/admin3.html` shows live data
- API key: `1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8`

## Git Push
- All commits pushed to `main` → `9f8d54c..02f3469`

## Design Doc
- **File:** `docs/superpowers/specs/2026-05-31-ihi-fanpage-integration-test.md`
- **Commit:** `f51aeee`

## Key Finding: RAG Re-recognition Behavior
- AIHub `/v1/ihi/analyze` flow: rule-based check first → if NORMAL, falls back to RAG pattern matching
- RAG only runs when sensor readings are NORMAL by rule thresholds
- Existing RAG cases in DB (case5, case 9) had broad patterns — test uses boundary values (t=84.9, v=2.0, c=50.0) that don't match any existing case
- After creating new RAG case with CRITICAL severity, same sensors return DANGER with case_id and confidence=0.6
- `devices` list only reflects rule-based results; RAG-only matches leave it empty but still upgrade alert level
