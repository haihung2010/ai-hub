#!/usr/bin/env python3
"""Seed 7 knowledge cards for the vehix project (rental policies, contracts, insurance).

Idempotent: re-running with the same titles is a no-op (checks existing
cards via GET /v1/knowledge/cards?project_id=vehix first).

Usage:
    ./venv/bin/python scripts/seed_vehix_rag.py            # real seed
    ./venv/bin/python scripts/seed_vehix_rag.py --dry-run  # count only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
MAIN_ROOT = Path("/home/hung/ai-hub")


def _load_key() -> str:
    # Try worktree .env first, then main repo .env
    for root in (ROOT, MAIN_ROOT):
        p = root / ".env"
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("API_KEY="):
                    k = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if k and len(k) > 20:
                        return k
    return ""


KEY = os.environ.get("AIHUB_KEY") or _load_key()
BASE = os.environ.get("AIHUB_BASE", "http://127.0.0.1:8000")
TENANT = "default"
PROJECT = "vehix"

# 7 cards covering the most common vehix queries.
# Each card: knowledge_domain, subdomain, title, summary, content, tags, trust_level.
CARDS = [
    {
        "knowledge_domain": "vehix",
        "subdomain": "policies",
        "title": "Phí gia hạn hợp đồng thuê xe",
        "summary": "Phí gia hạn dao động 50-150k/ngày tùy loại xe (số/ga/điện)",
        "content": (
            "Phí gia hạn hợp đồng thuê xe dao động 50.000-150.000đ/ngày tùy loại xe:\n"
            "- Xe số (Wave, Dream): 50.000-80.000đ/ngày\n"
            "- Xe ga (Vision, Lead): 80.000-120.000đ/ngày\n"
            "- Xe điện (VF3, VF8): 100.000-150.000đ/ngày\n\n"
            "Gia hạn tối đa 7 ngày qua app hoặc liên hệ CSKH. Sau 7 ngày phải ký hợp đồng mới.\n"
            "Phí tính theo ngày, không tính theo giờ. Áp dụng cho cả hợp đồng ngắn hạn và dài hạn."
        ),
        "tags": ["rental", "extension", "fee", "policy"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "vehix",
        "subdomain": "policies",
        "title": "Phí trả xe trễ giờ",
        "summary": "Trễ 1-3h: 50k/giờ. Trễ 3h+: tính 1 ngày. Trễ 6h+: thêm 30% phí ngày",
        "content": (
            "Phí trả xe trễ giờ:\n"
            "- Trễ 1-3 giờ: 50.000đ/giờ (mỗi giờ lẻ tính tròn)\n"
            "- Trễ trên 3 giờ: tính thành 1 ngày thuê mới\n"
            "- Trễ trên 6 giờ: thêm 30% phí ngày\n\n"
            "Khuyến nghị gọi CSKH trước 2 giờ nếu biết sẽ trễ để được giảm 50% phí trễ.\n"
            "Trường hợp đặc biệt (thiên tai, tai nạn) được miễn phí trễ khi có giấy tờ chứng minh."
        ),
        "tags": ["rental", "late", "fee", "policy"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "vehix",
        "subdomain": "policies",
        "title": "Quy trình đặt cọc thuê xe",
        "summary": "Cọc 30-50% giá trị xe. Hoàn trả trong 24h sau khi trả xe OK",
        "content": (
            "Đặt cọc khi ký hợp đồng thuê xe:\n"
            "- Xe số: cọc tối thiểu 30% giá trị xe (tối thiểu 2.000.000đ)\n"
            "- Xe ga: cọc tối thiểu 40% giá trị xe\n"
            "- Xe điện: cọc tối thiểu 50% giá trị xe\n\n"
            "Cọc hoàn trả trong 24h sau khi trả xe và đối chiếu xe OK.\n"
            "Thanh toán: tiền mặt, chuyển khoản, thẻ tín dụng (Visa/Master/JCB).\n"
            "Trường hợp vi phạm hợp đồng: khấu trừ từ cọc theo điều khoản."
        ),
        "tags": ["rental", "deposit", "payment", "policy"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "vehix",
        "subdomain": "contracts",
        "title": "Hợp đồng thuê xe số (Wave, Dream)",
        "summary": "Thuê tối thiểu 1 ngày, tối đa 30 ngày. Giá 100-150k/ngày",
        "content": (
            "Điều khoản thuê xe số (Honda Wave, Dream):\n"
            "- Thời hạn thuê tối thiểu 1 ngày, tối đa 30 ngày\n"
            "- Giá thuê: 100.000-150.000đ/ngày (tùy đời xe)\n"
            "- Bảo hiểm TNDS bắt buộc đi kèm\n"
            "- Khách tự đổ xăng, công ty không chịu trách nhiệm về xăng\n"
            "- Hợp đồng có hiệu lực sau khi cọc được thanh toán đủ\n"
            "- GPLX bắt buộc (hạng A1 trở lên cho xe số)"
        ),
        "tags": ["rental", "scooter", "contract", "wave", "dream"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "vehix",
        "subdomain": "contracts",
        "title": "Hợp đồng thuê xe ga (Vision, Lead)",
        "summary": "Thuê tối thiểu 1 ngày, tối đa 14 ngày. Giá 150-250k/ngày",
        "content": (
            "Điều khoản thuê xe ga (Honda Vision, Lead, SH Mode):\n"
            "- Thời hạn thuê tối thiểu 1 ngày, tối đa 14 ngày\n"
            "- Giá thuê: 150.000-250.000đ/ngày\n"
            "- Bảo hiểm TNDS bắt buộc + bảo hiểm vật chất xe (khuyến nghị)\n"
            "- Khách được đổ xăng đầy bình khi nhận xe, trả xe với mức xăng tương đương\n"
            "- Giấy tờ cần: CMND/CCCD + GPLX hợp lệ (hạng A1 cho Vision/Lead)\n"
            "- Phụ thu 50.000đ/ngày nếu khách muốn trả xe sau 18:00"
        ),
        "tags": ["rental", "automatic", "contract", "vision", "lead"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "vehix",
        "subdomain": "contracts",
        "title": "Hợp đồng thuê xe điện VinFast (VF3, VF8)",
        "summary": "Thuê 1-7 ngày. VF3: 600-800k/ngày. VF8: 1.2-1.5M/ngày. Pin kèm xe",
        "content": (
            "Điều khoản thuê xe điện VinFast (VF3, VF8, VF9):\n"
            "- Thời hạn thuê tối thiểu 1 ngày, tối đa 7 ngày (xe mới)\n"
            "- Giá thuê VF3: 600.000-800.000đ/ngày; VF8: 1.200.000-1.500.000đ/ngày\n"
            "- Pin kèm theo xe (còn tối thiểu 80% khi nhận)\n"
            "- Khách tự sạc pin tại hệ thống V-Green/Charging Stations\n"
            "- Phí sạc do khách tự chi trả; thời gian sạc đầy VF3 ~1.5h, VF8 ~3h\n"
            "- Bắt buộc có GPLX và đặt cọc 50% giá trị xe\n"
            "- Không giới hạn km, nhưng phụ thu nếu vượt 300km/ngày"
        ),
        "tags": ["rental", "ev", "contract", "vinfast", "vf3", "vf8"],
        "trust_level": 5,
    },
    {
        "knowledge_domain": "vehix",
        "subdomain": "insurance",
        "title": "Bảo hiểm thuê xe — loại và mức bồi thường",
        "summary": "TNDS bắt buộc (150tr) + vật chất tự nguyện (theo giá trị xe)",
        "content": (
            "Bảo hiểm áp dụng khi thuê xe:\n\n"
            "1. Bảo hiểm TNDS bắt buộc (Bảo Việt, PVI, Bảo Minh):\n"
            "   - Mức bồi thường: tối đa 150 triệu đồng/vụ cho người thứ 3\n"
            "   - Đã bao gồm trong giá thuê\n"
            "   - Áp dụng cho cả xe số, xe ga, xe điện\n\n"
            "2. Bảo hiểm vật chất xe (tự nguyện, khuyến nghị):\n"
            "   - Mức bồi thường: theo giá trị xe (khấu hao 1.5%/tháng)\n"
            "   - Phí thêm: 5-10% giá thuê/ngày\n"
            "   - Trường hợp loại trừ: say xỉn, không có GPLX, vi phạm luật giao thông\n\n"
            "3. Thủ tục bồi thường: thông báo trong 24h, cung cấp biên bản công an (nếu có)\n"
            "   - Liên hệ hotline bảo hiểm: 1900-xxxx (in trên hợp đồng)\n"
            "   - Giữ nguyên hiện trường, không tự ý sửa chữa trước khi có bảo hiểm khảo sát"
        ),
        "tags": ["rental", "insurance", "claim", "tnds"],
        "trust_level": 5,
    },
]


def _post_card(card: dict) -> tuple[bool, str]:
    """POST a single card to the knowledge cards API. Returns (ok, message)."""
    payload = {
        "tenant_id": TENANT,
        "project_id": PROJECT,
        **card,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/v1/knowledge/cards",
        data=data,
        headers={"Content-Type": "application/json", "X-API-KEY": KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return True, f"created id={json.loads(r.read().decode())['card']['id']}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return False, f"HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return False, repr(e)[:200]


def _existing_titles() -> set[str]:
    """Return the set of card titles already present in the vehix project."""
    req = urllib.request.Request(
        f"{BASE}/v1/knowledge/cards?project_id={PROJECT}&tenant_id={TENANT}&limit=200",
        headers={"X-API-KEY": KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            return {c["title"] for c in data.get("cards", [])}
    except Exception:
        return set()


def main():
    parser = argparse.ArgumentParser(description="Seed vehix knowledge base")
    parser.add_argument("--dry-run", action="store_true", help="count cards without writing")
    args = parser.parse_args()

    if not KEY:
        sys.exit("AIHUB_KEY env var or API_KEY in .env required")

    print(f"Seeding {len(CARDS)} cards for project={PROJECT} tenant={TENANT}")
    if args.dry_run:
        print("DRY RUN — no DB writes")
        return 0

    existing = _existing_titles()
    print(f"Found {len(existing)} existing cards for {PROJECT}")

    inserted = 0
    skipped = 0
    failed = 0
    for card in CARDS:
        if card["title"] in existing:
            print(f"  ⊝ SKIP {card['title'][:60]} (already exists)")
            skipped += 1
            continue
        ok, msg = _post_card(card)
        status = "✓" if ok else "✗"
        print(f"  {status} {card['title'][:60]}: {msg[:100]}")
        if ok:
            inserted += 1
        else:
            failed += 1

    print(f"\nSummary: {inserted} inserted, {skipped} skipped, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
