"""
Test Fanpage continuous multi-turn chat: 20 cycles x 5 messages = 100 calls.
Run: python scripts/test_fanpage_continuous.py
"""
import asyncio
import httpx
import time

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL = "http://localhost:8000"
TIMEOUT = 60.0
CYCLES = 20

CONVERSATION_CYCLE = [
    "Xin chào",
    "Cho tôi hỏi về sản phẩm A",
    "Giá bao nhiêu?",
    "Cảm ơn",
    "Sản phẩm B có gì khác?",
]


async def send_message(client, user_message, cycle, msg_idx):
    start = time.time()
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "fanpage",
                "user_name": "fanpage-user-001",
                "user_message": user_message,
                "stream": False,
            }
        )
        latency_ms = (time.time() - start) * 1000
        resp.raise_for_status()
        result = resp.json()
        content = result.get("content", "")
        return {
            "cycle": cycle,
            "msg_idx": msg_idx,
            "latency_ms": latency_ms,
            "response_length": len(content),
            "content": content,
            "error": None,
        }
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        return {
            "cycle": cycle,
            "msg_idx": msg_idx,
            "latency_ms": latency_ms,
            "response_length": 0,
            "content": "",
            "error": str(e),
        }


async def main():
    print("=" * 60)
    print("Fanpage Continuous Chat Test")
    print(f"Running {CYCLES} cycles x {len(CONVERSATION_CYCLE)} messages = {CYCLES * len(CONVERSATION_CYCLE)} total calls")
    print("=" * 60)

    results = []
    errors = 0
    empty_responses = 0
    latencies = []
    response_contents = []

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for cycle in range(1, CYCLES + 1):
            for msg_idx, user_message in enumerate(CONVERSATION_CYCLE):
                r = await send_message(client, user_message, cycle, msg_idx)
                results.append(r)
                latencies.append(r["latency_ms"])
                if r["error"]:
                    errors += 1
                    print(f"  [C{cycle}.{msg_idx+1}] ERROR: {r['error']}")
                elif not r["content"]:
                    empty_responses += 1
                    print(f"  [C{cycle}.{msg_idx+1}] EMPTY RESPONSE")
                else:
                    response_contents.append(r["content"])
                    print(f"  [C{cycle}.{msg_idx+1}] {r['latency_ms']:.0f}ms, {len(r['content'])} chars")

    # Summary
    total = CYCLES * len(CONVERSATION_CYCLE)
    p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0
    unique_responses = len(set(response_contents))

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total calls:   {total}")
    print(f"HTTP errors:   {errors}")
    print(f"Empty resp:   {empty_responses}")
    print(f"p95 latency:   {p95_latency:.0f}ms")
    print(f"Unique resp:   {unique_responses} (out of {len(response_contents)} non-empty)")

    pass_http = errors == 0
    pass_empty = empty_responses == 0
    pass_latency = p95_latency < 10000
    pass_diversity = unique_responses >= 3

    print(f"\n  HTTP 200 on all calls: {'PASS' if pass_http else 'FAIL'}")
    print(f"  No empty responses:    {'PASS' if pass_empty else 'FAIL'}")
    print(f"  p95 latency < 10s:      {'PASS' if pass_latency else 'FAIL'}")
    print(f"  >= 3 distinct resp:    {'PASS' if pass_diversity else 'FAIL'}")

    all_pass = pass_http and pass_empty and pass_latency and pass_diversity
    print(f"\nOVERALL: {'ALL PASS' if all_pass else 'SOME FAILURES'}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
