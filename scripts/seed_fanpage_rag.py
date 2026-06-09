#!/usr/bin/env python3
"""Seed initial RAG cards for fanpage project.

Provides product info, pricing, shipping policy, return policy, and warranty
so E2B-bg / 12B / 4B models can answer customer questions with real context
instead of deflecting "sản phẩm gì? tên sản phẩm?".

Usage:
    ./venv/bin/python scripts/seed_fanpage_rag.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent


def _load_key() -> str:
    p = ROOT / ".env"
    for line in p.read_text().splitlines():
        if line.startswith("API_KEY="):
            k = line.split("=", 1)[1].strip().strip('"').strip("'")
            if k and len(k) > 20:
                return k
    return ""


KEY = os.environ.get("AIHUB_KEY") or _load_key()
BASE = os.environ.get("AIHUB_BASE", "http://127.0.0.1:8000")
TENANT = "realistic_day"

SEED_CARDS = [
    # === Product catalog ===
    {
        "knowledge_domain": "products",
        "title": "Serum Vitamin C 20% (Sản phẩm A)",
        "summary": "Serum Vitamin C nồng độ 20% cho da xỉn màu, dùng buổi sáng trước kem chống nắng",
        "content": (
            "Tên: Serum Vitamin C 20% (sản phẩm A)\n"
            "Giá: 450,000 VND / 30ml\n"
            "Thành phần chính: L-Ascorbic Acid 20%, Vitamin E 1%, Ferulic Acid 0.5%, Hyaluronic Acid\n"
            "Cách dùng: Nhỏ 3-4 giọt lên mặt sạch, vỗ nhẹ, dùng buổi sáng trước kem chống nắng\n"
            "Phù hợp: Da xỉn màu, da tối màu, có nếp nhăn nhẹ\n"
            "Không phù hợp: Da quá nhạy cảm với acid, vết thương hở\n"
            "Bảo quản: Nơi khô ráo, tránh ánh sáng, dùng trong 6 tháng sau khi mở"
        ),
        "tags": ["vitamin-c", "serum", "chống-lão-hóa", "sáng-da"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "products",
        "title": "Kem chống nắng SPF50+ PA++++ (Sản phẩm B)",
        "summary": "Kem chống nắng vật lý lai hóa học, không nhờn rít, dùng hàng ngày",
        "content": (
            "Tên: Kem chống nắng SPF50+ PA++++ (sản phẩm B)\n"
            "Giá: 380,000 VND / 50ml\n"
            "Loại: Vật lý lai hóa học (Zinc Oxide 15% + Tinosorb S)\n"
            "Đặc điểm: Không nhờn rít, không để lại vệt trắng, finish mịn\n"
            "Phù hợp: Mọi loại da, kể cả da nhạy cảm\n"
            "Cách dùng: Thoa đều 2mg/cm² da (~1/4 thìa cà phê cho mặt) 15 phút trước khi ra nắng, reapply mỗi 2 giờ\n"
            "Bảo quản: Tránh nhiệt độ cao, đậy nắp sau khi dùng"
        ),
        "tags": ["kem-chống-nắng", "spf50", "pa++++", "hàng-ngày"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "products",
        "title": "Retinol 0.5% serum (Sản phẩm C)",
        "summary": "Retinol nồng độ trung bình cho người đã dùng quen, chống lão hóa chuyên sâu",
        "content": (
            "Tên: Retinol 0.5% serum (sản phẩm C)\n"
            "Giá: 520,000 VND / 30ml\n"
            "Nồng độ: 0.5% Retinol + Squalane + Vitamin E\n"
            "Phù hợp: Đã dùng retinol trước đó, da lão hóa rõ (nếp nhăn, sạm, kém đàn hồi)\n"
            "Không phù hợp: Người mới bắt đầu, da nhạy cảm, đang mang thai/cho con bú\n"
            "Cách dùng: Buổi tối, sau serum Vitamin C, 2-3 lần/tuần đầu, tăng dần lên hàng ngày\n"
            "Lưu ý: Bắt buộc dùng kem chống nắng ban ngày, không dùng chung với AHA/BHA cùng tối\n"
            "Bảo quản: Tủ lạnh, dùng trong 3 tháng sau khi mở"
        ),
        "tags": ["retinol", "chống-lão-hóa", "serum", "ban-đêm"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "products",
        "title": "Toner BHA 2% (Sản phẩm D)",
        "summary": "Toner BHA cho da dầu mụn, thông thoáng lỗ chân lông",
        "content": (
            "Tên: Toner BHA 2% (sản phẩm D)\n"
            "Giá: 290,000 VND / 200ml\n"
            "Thành phần: Salicylic Acid 2%, Niacinamide 4%, Zinc PCA\n"
            "Phù hợp: Da dầu, da mụn, lỗ chân lông to, da có mụn đầu đen\n"
            "Cách dùng: Sau rửa mặt, dùng bông tẩm toner lau đều, hoặc pour vào lòng bàn tay vỗ\n"
            "Tần suất: Mỗi tối, hoặc 2 lần/ngày nếu da chịu tốt\n"
            "Không dùng chung: Vitamin C cùng buổi, retinol cùng tối (cách nhau 30 phút)\n"
            "Kết quả: Sau 4 tuần lỗ chân lông thấy sạch hơn, sau 8 tuần giảm mụn đầu đen"
        ),
        "tags": ["bha", "toner", "da-dầu", "mụn"],
        "trust_level": 5,
    },

    # === Policies ===
    {
        "knowledge_domain": "policies",
        "title": "Chính sách đổi trả trong 7 ngày",
        "summary": "Đổi trả miễn phí trong 7 ngày nếu sản phẩm lỗi hoặc giao sai",
        "content": (
            "Điều kiện đổi trả:\n"
            "- Trong vòng 7 ngày kể từ ngày nhận hàng\n"
            "- Sản phẩm còn nguyên seal, chưa sử dụng (trừ lỗi NSX)\n"
            "- Hoặc sản phẩm bị lỗi do nhà sản xuất\n"
            "- Hoặc giao sai màu/size/sản phẩm so với đơn đặt\n\n"
            "Quy trình:\n"
            "1. Liên hệ fanpage kèm mã đơn + ảnh sản phẩm\n"
            "2. Shop xác nhận trong 24h\n"
            "3. Gửi hàng về shop (shop chịu phí ship nếu lỗi từ shop)\n"
            "4. Shop gửi hàng mới hoặc hoàn tiền trong 3-5 ngày làm việc\n\n"
            "Không áp dụng: Sản phẩm khuyến mãi clearance, sản phẩm đã dùng >30%"
        ),
        "tags": ["đổi-trả", "bảo-hành", "chính-sách"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "policies",
        "title": "Chính sách vận chuyển và phí ship",
        "summary": "Free ship nội thành đơn từ 500K, hỗ trợ ship COD toàn quốc",
        "content": (
            "Phí vận chuyển:\n"
            "- Nội thành Hà Nội, HCM: 25,000 VND — FREE cho đơn ≥ 500,000 VND\n"
            "- Tỉnh khác: 35,000-45,000 VND tùy khu vực, FREE cho đơn ≥ 800,000 VND\n"
            "- Vùng sâu vùng xa: 55,000-70,000 VND\n\n"
            "Thời gian giao hàng:\n"
            "- Nội thành: 1-2 ngày (đặt trước 14h giao trong ngày)\n"
            "- Tỉnh: 2-4 ngày\n"
            "- Vùng sâu: 4-7 ngày\n\n"
            "Hình thức thanh toán:\n"
            "- COD (thanh toán khi nhận hàng) — phí thu hộ 10,000 VND\n"
            "- Chuyển khoản trước — FREE ship thêm 5,000 VND\n"
            "- Ví MoMo, ZaloPay, ShopeePay — tích điểm 2%"
        ),
        "tags": ["vận-chuyển", "ship", "cod", "chính-sách"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "policies",
        "title": "Bảo hành sản phẩm chính hãng",
        "summary": "Cam kết 100% chính hãng, bảo hành chất lượng theo từng dòng sản phẩm",
        "content": (
            "Cam kết chính hãng:\n"
            "- 100% sản phẩm có bill nhập khẩu từ hãng, có check QR trên bao bì\n"
            "- Không bán hàng sample, hàng trưng bày, hàng cận date\n"
            "- Hạn sử dụng còn ≥ 2/3 thời hạn khi giao (trừ sản phẩm short-date được thông báo)\n\n"
            "Bảo hành:\n"
            "- Serum, toner: Không bảo hành sau khi mở seal (vì lý do vệ sinh)\n"
            "- Bao bì lỗi, pump hỏng, seal bung: Đổi mới trong 14 ngày\n"
            "- Kem chống nắng: Bảo hành kết cấu sản phẩm, nếu bị tách nước/đổi màu bất thường đổi mới\n\n"
            "Xử lý khiếu nại: Phản hồi trong 4h làm việc, giải quyết trong 24h"
        ),
        "tags": ["bảo-hành", "chính-hãng", "chính-sách"],
        "trust_level": 5,
    },

    # === Promotions ===
    {
        "knowledge_domain": "promotions",
        "title": "Khuyến mãi tháng 6 — Mua 2 tặng 1",
        "summary": "Chương trình mua 2 tặng 1 cho dòng serum, áp dụng đến 30/06",
        "content": (
            "Chương trình: Mua 2 tặng 1 serum bất kỳ\n"
            "Áp dụng: Tất cả serum (Vitamin C, Retinol, BHA, Niacinamide)\n"
            "Thời gian: 01/06 - 30/06/2026\n"
            "Cách tham gia: Tự động áp dụng khi thêm 3 sản phẩm serum vào giỏ, hệ thống tặng sản phẩm có giá trị thấp nhất\n"
            "Sản phẩm tặng: Có thể chọn khác giá trị bằng cách chat trực tiếp với shop\n"
            "Không áp dụng: Đơn hàng đã áp mã giảm giá khác, đơn wholesale\n"
            "Mã code: Không cần, hệ thống tự động"
        ),
        "tags": ["khuyến-mãi", "mua-2-tặng-1", "serum"],
        "trust_level": 4,
    },
    {
        "knowledge_domain": "promotions",
        "title": "Combo tiết kiệm — Bộ skincare cơ bản",
        "summary": "Combo 4 sản phẩm thiết yếu cho người mới, giảm 25% so với mua lẻ",
        "content": (
            "Combo cơ bản gồm:\n"
            "- Sữa rửa mặt dịu nhẹ (150ml): 180,000 VND\n"
            "- Toner BHA 2% (sản phẩm D, 200ml): 290,000 VND\n"
            "- Serum Vitamin C 20% (sản phẩm A, 30ml): 450,000 VND\n"
            "- Kem chống nắng SPF50+ (sản phẩm B, 50ml): 380,000 VND\n\n"
            "Giá lẻ: 1,300,000 VND\n"
            "Giá combo: 975,000 VND (giảm 25%)\n\n"
            "Phù hợp: Người mới bắt đầu skincare, muốn bộ cơ bản đầy đủ\n"
            "Tặng kèm: 1 túi vải đựng mỹ phẩm + sample kem dưỡng ẩm mini 5ml"
        ),
        "tags": ["combo", "skincare-cơ-bản", "tiết-kiệm"],
        "trust_level": 4,
    },
]


def _post_card(card: dict) -> dict:
    payload = {
        "project_id": "fanpage",
        "tenant_id": TENANT,
        **card,
    }
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"{BASE}/v1/knowledge/cards",
        data=data,
        headers={"Content-Type": "application/json", "X-API-KEY": KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        return {"error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"error": repr(e)}


def main() -> int:
    if not KEY:
        print("FATAL: API_KEY not set", file=sys.stderr)
        return 1

    # Check existing cards first
    print(f"Listing existing fanpage cards under tenant={TENANT}...")
    list_req = urllib.request.Request(
        f"{BASE}/v1/knowledge/cards?project_id=fanpage&tenant_id={TENANT}&limit=200",
        headers={"X-API-KEY": KEY},
    )
    try:
        with urllib.request.urlopen(list_req, timeout=10) as r:
            existing = json.loads(r.read().decode())
            n_existing = len(existing.get("cards", []))
            print(f"  Found {n_existing} existing card(s)")
    except Exception as e:
        print(f"  WARN: could not list existing cards: {e!r}")
        n_existing = 0

    if n_existing >= len(SEED_CARDS):
        print(f"  Already have {n_existing} cards ≥ {len(SEED_CARDS)} — skipping seed")
        return 0

    print(f"\nSeeding {len(SEED_CARDS)} fanpage RAG cards...")
    ok = 0
    for i, card in enumerate(SEED_CARDS, 1):
        result = _post_card(card)
        if "error" in result:
            print(f"  [{i}/{len(SEED_CARDS)}] FAIL: {card['title'][:40]} → {result['error']}")
        else:
            card_id = (result.get("card") or {}).get("id", "?")
            print(f"  [{i}/{len(SEED_CARDS)}] OK: {card['title'][:50]} (id={card_id})")
            ok += 1

    print(f"\nDone: {ok}/{len(SEED_CARDS)} cards seeded")
    return 0 if ok == len(SEED_CARDS) else 2


if __name__ == "__main__":
    sys.exit(main())
