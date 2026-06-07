"""Vietnamese quality scoring for benchmark responses.

LLM-as-judge would be ideal but is too slow for benchmarks. Use heuristics:
- detect_hallucination: known bad tokens
- score_response: heuristic rubric 1-10

For the actual benchmark, this is a placeholder — replace with real LLM judge
in the orchestrator if time permits. For now, heuristic catches the worst cases.
"""
from __future__ import annotations


HALLUCINATION_MARKERS = [
    "ArrayList",
    "CLASS-NORMAL",
    "[object Object]",
    "null",
    "undefined",
    "NaN, NaN",
]


def detect_hallucination(response: str) -> bool:
    """Return True if response contains known hallucination markers or is empty."""
    if not response or len(response.strip()) < 5:
        return True
    return any(marker in response for marker in HALLUCINATION_MARKERS)


# 28 Vietnamese prompts
PROMPT_BANK = [
    {"id": 1, "category": "greeting", "prompt": "Xin chào, bạn tên gì?"},
    {"id": 2, "category": "greeting", "prompt": "Bạn có khỏe không?"},
    {"id": 3, "category": "greeting", "prompt": "Hôm nay bạn thế nào?"},
    {"id": 4, "category": "greeting", "prompt": "Cảm ơn bạn"},
    {"id": 5, "category": "technical", "prompt": "Giải thích NEMA MG-1 voltage imbalance threshold"},
    {"id": 6, "category": "technical", "prompt": "ISO 10816-3 vibration zones là gì?"},
    {"id": 7, "category": "technical", "prompt": "Phân biệt cảm biến IoT và cảm biến công nghiệp"},
    {"id": 8, "category": "technical", "prompt": "I2C vs SPI, nên chọn loại nào cho sensor?"},
    {"id": 9, "category": "technical", "prompt": "Tại sao cần pull-up resistor cho I2C?"},
    {"id": 10, "category": "technical", "prompt": "Giải thích MQTT QoS levels"},
    {"id": 11, "category": "code", "prompt": "Sửa lỗi: `def f(x): return x + 1` cho list"},
    {"id": 12, "category": "code", "prompt": "Viết hàm Python kiểm tra số nguyên tố"},
    {"id": 13, "category": "code", "prompt": "Sự khác biệt giữa `==` và `is` trong Python?"},
    {"id": 14, "category": "code", "prompt": "Cách đọc file JSON trong Python?"},
    {"id": 15, "category": "translation", "prompt": "Dịch 'industrial sensor monitoring' sang tiếng Việt"},
    {"id": 16, "category": "translation", "prompt": "Translate 'predictive maintenance' to Vietnamese"},
    {"id": 17, "category": "translation", "prompt": "Dịch 'cảm biến rung động' sang tiếng Anh"},
    {"id": 18, "category": "translation", "prompt": "'Edge computing' tiếng Việt là gì?"},
    {"id": 19, "category": "factual", "prompt": "Tại sao bầu trời có màu xanh?"},
    {"id": 20, "category": "factual", "prompt": "Thủ đô Việt Nam là gì?"},
    {"id": 21, "category": "factual", "prompt": "Dân số Việt Nam hiện tại khoảng bao nhiêu?"},
    {"id": 22, "category": "factual", "prompt": "AI là gì? Giải thích ngắn gọn"},
    {"id": 23, "category": "creative", "prompt": "Viết 1 đoạn văn 4 câu về IoT trong nông nghiệp"},
    {"id": 24, "category": "creative", "prompt": "Hãy sáng tác 1 bài thơ 4 dòng về cảm biến"},
    {"id": 25, "category": "creative", "prompt": "Mô tả 1 ngày làm việc của kỹ sư IoT"},
    {"id": 26, "category": "reasoning", "prompt": "Có 5 quả táo, cho 2 bạn mỗi bạn 1 quả. Còn mấy?"},
    {"id": 27, "category": "reasoning", "prompt": "Nếu A > B và B > C, thì A > C? Tại sao?"},
    {"id": 28, "category": "reasoning", "prompt": "Tại sao 1 + 1 = 2?"},
]


def score_response(prompt: str, response: str) -> dict:
    """Heuristic quality scoring 1-10.

    Returns dict with breakdown: {relevance, naturalness, accuracy, conciseness, format, total}
    """
    if detect_hallucination(response):
        return {"relevance": 0, "naturalness": 0, "accuracy": 0, "conciseness": 0, "format": 0, "total": 0}

    relevance = 0
    naturalness = 0
    accuracy = 0
    conciseness = 0
    format_score = 0

    # Heuristic: response length correlates with thoroughness
    words = len(response.split())
    if words >= 5:
        relevance = 1
    if words >= 20:
        relevance = 2
    if any(kw in response.lower() for kw in prompt.lower().split() if len(kw) > 3):
        relevance = min(relevance + 1, 3)

    # Naturalness: look for Vietnamese diacritics
    vi_chars = sum(1 for c in response if c in "ăâđêôơưĂÂĐÊÔƠƯáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
    if vi_chars > 0:
        naturalness = 1
    if vi_chars > 20:
        naturalness = 2

    # Accuracy: cannot verify without ground truth — assume average
    accuracy = 2 if words >= 20 else 1

    # Conciseness: not too short, not too long
    if 20 <= words <= 200:
        conciseness = 1

    # Format: has proper punctuation
    if response.rstrip().endswith((".", "!", "?")):
        format_score = 1

    total = relevance + naturalness + accuracy + conciseness + format_score
    return {
        "relevance": relevance,
        "naturalness": naturalness,
        "accuracy": accuracy,
        "conciseness": conciseness,
        "format": format_score,
        "total": total,
    }
