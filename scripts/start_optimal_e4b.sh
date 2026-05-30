# AI Hub Working Configuration - 2026-05-28
# RTX 5060 Ti 16GB - Optimal Setup

# ===== MODELS =====
# Port 8080: E4B Q4_K_M - Primary chat (8 slots, 131K spec ctx)
# Port 8081: E2B Q4_K_M - Background extraction (4 slots)
# Port 8082: Reranker Q8 - Knowledge reranking (1 slot)

# NOTE: E4B GGUF Q4_K_M actual context ~4K tokens (not 128K spec)
# For IHI: batch 15 devices/call for best results

# ===== E4B PRIMARY (port 8080) =====
nohup /home/hung/llama.cpp/build-cuda13/bin/llama-server \
  -m /home/hung/models/gemma-4-E4B-it-obliterated-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8080 --ctx-size 131072 --parallel 8 --n-gpu-layers 999 \
  --alias local-gemma4-e4b-q4 --reasoning off --flash-attn on --cont-batching \
  >/tmp/llama-8080-e4b.log 2>&1 &

# ===== E2B BACKGROUND (port 8081) =====
nohup /home/hung/llama.cpp/build-cuda13/bin/llama-server \
  -m /home/hung/models/gemma-4-E2B-it-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8081 --ctx-size 131072 --parallel 4 --n-gpu-layers 999 \
  --alias local-gemma4-e2b-q4-bg --reasoning off --flash-attn on --cont-batching \
  >/tmp/llama-8081-e2b.log 2>&1 &

# ===== RERANKER (port 8082) =====
nohup /home/hung/llama.cpp/build-cuda13/bin/llama-server \
  -m /home/hung/models/bge-reranker-v2-m3-q8_0.gguf --reranking \
  --host 127.0.0.1 --port 8082 --ctx-size 4096 --n-gpu-layers 999 \
  --alias local-reranker-q8 \
  >/tmp/llama-8082-reranker.log 2>&1 &

# ===== AI HUB =====
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 >/tmp/aihub.log 2>&1 &

# ===== .ENV SETTINGS FOR IHI =====
# PROJECT_CONTEXT_SIZES={"ihi": 100000}
# AI_MAX_TOKENS=4000
# LOCAL_MAX_TOKENS=4000

# ===== IHI PROJECT =====
# Prompt: app/prompts/ihi.md (English)
# Batch: 15 devices/call for 45 sensors
# Latency: ~3-5s per batch
