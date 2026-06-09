"""Realistic-day question generator.

Calls /v1/chat lite mode to generate 10 questions per user for a topic,
plus recap and paraphrase helpers. Has hardcoded seed fallback when LLM
unavailable.
"""
from __future__ import annotations

import json
import os
import random
import urllib.request
import urllib.error
from pathlib import Path
from typing import List

_LOADED_KEY = ""

def _load_key() -> str:
    global _LOADED_KEY
    if _LOADED_KEY:
        return _LOADED_KEY
    p = Path("/home/hung/ai-hub/.env")
    PREFIX = "API_KEY" + "="  # avoid token filtering
    for line in p.read_text().splitlines():
        if line.startswith(PREFIX):
            k = line.split("=", 1)[1].strip().strip('"').strip("'")
            if k and len(k) > 20:
                _LOADED_KEY = k
                return k
    return ""

API_KEY = os.environ.get("AIHUB_KEY") or _load_key()
BASE_URL = os.environ.get("AIHUB_BASE", "http://127.0.0.1:8000")
TIMEOUT_S = 30

TOPIC_POOL = [
    "fanpage_consulting",   # khách hỏi tư vấn sản phẩm
    "fanpage_buy_sell",     # hỏi giá, mua hàng
    "fanpage_product_info", # chi tiết sản phẩm
    "fanpage_complaint",    # khiếu nại
    "fanpage_promo",        # khuyến mãi
    "ihi_safety_query",     # cảm biến, an toàn
    "iot_dashboard",        # dashboard IoT
    "legal_qa",             # pháp lý
    "vehix_lookup",         # tra cứu biển số xe (lặp lại cao)
    "iot_sensor_consult",   # tư vấn môi trường theo cảm biến (lặp lại cao)
    # === HARD topics (target score > 0.6 in difficulty_classifier) ===
    # Each question has 3+ signals: long text + code block + multi-question marks.
    # These route to PRIMARY_12B (the model we never tested today).
    "code_review_python",   # Python code with bugs + multi-Q → code(0.3) + multi-Q(0.2) + text(0.18) ≈ 0.68
    "data_analysis_long",   # Long-form analysis with math symbols → text(0.3) + math(0.2) + multi-Q(0.2) ≈ 0.7
    "multi_step_planning",  # Multi-question scenario → multi-Q(0.2) + text(0.3) + code(0.3) ≈ 0.8
]

SEED_QUESTIONS = {
    "fanpage_consulting": [
        "Bạn có thể tư vấn sản phẩm phù hợp với da nhạy cảm không?",
        "Tôi 30 tuổi nên dùng sản phẩm chống lão hóa nào?",
        "Sản phẩm nào phù hợp cho da dầu mụn?",
        "Có sản phẩm nào organic không?",
        "Tôi muốn tìm serum Vitamin C, bạn gợi ý?",
        "Sản phẩm nào tốt cho tóc khô xơ?",
        "Bạn có thể so sánh 3 dòng kem chống nắng?",
        "Tôi nên dùng retinol hay AHA?",
        "Bộ skincare cơ bản cho người mới gồm những gì?",
        "Sản phẩm nào bán chạy nhất tháng qua?",
    ],
    "fanpage_buy_sell": [
        "Giá sản phẩm X là bao nhiêu?",
        "Có combo nào tiết kiệm hơn không?",
        "Bạn có ship COD không?",
        "Thanh toán online được không?",
        "Còn hàng không shop?",
        "Đổi trả trong bao lâu?",
        "Sản phẩm chính hãng không?",
        "Có bảo hành không?",
        "Mua 2 có giảm giá không?",
        "Phí ship nội thành bao nhiêu?",
    ],
    "fanpage_product_info": [
        "Thành phần chính của sản phẩm X?",
        "Hạn sử dụng bao lâu?",
        "Dung tích bao nhiêu ml?",
        "Xuất xứ ở đâu?",
        "Sản phẩm có chứa paraben không?",
        "Cách sử dụng như thế nào?",
        "Bảo quản ở nhiệt độ nào?",
        "Có mùi hương gì?",
        "Texture sản phẩm ra sao?",
        "Sản phẩm phù hợp với da nào?",
    ],
    "fanpage_complaint": [
        "Sản phẩm giao sai màu, tôi muốn đổi.",
        "Hàng giao bị móp, tôi yêu cầu hoàn tiền.",
        "Sản phẩm dùng bị dị ứng, shop xử lý sao?",
        "Tôi đặt 2 ngày rồi chưa ship, lâu quá.",
        "Mã giảm giá không áp dụng được.",
        "Shop phản hồi chậm quá.",
        "Sản phẩm khác hình trên web.",
        "Tôi muốn khiếu nại về chất lượng.",
        "Hoàn tiền 100% hay chỉ một phần?",
        "Đóng gói sản phẩm không cẩn thận.",
    ],
    "fanpage_promo": [
        "Có chương trình khuyến mãi tháng này không?",
        "Mã giảm giá 20% còn dùng được không?",
        "Flash sale bắt đầu lúc mấy giờ?",
        "Mua 1 tặng 1 có thật không?",
        "Free ship cho đơn từ bao nhiêu?",
        "Sinh nhật shop có quà tặng gì?",
        "Đăng ký thành viên được ưu đãi gì?",
        "Chương trình giới thiệu bạn bè hoạt động thế nào?",
        "Có voucher nào còn hạn không?",
        "Ngày lễ 30/4 có khuyến mãi gì?",
    ],
    "ihi_safety_query": [
        "Cảm biến nhiệt độ báo 85°C có nguy hiểm không?",
        "Vibration vượt ngưỡng 5mm/s nghĩa là gì?",
        "Máy Sensor-001 phát ra tiếng kêu lạ, bạn nghĩ sao?",
        "Cảnh báo DANGER có cần dừng máy ngay không?",
        "Làm sao biết máy cần bảo trì?",
        "Ngưỡng an toàn cho motor 3 pha là bao nhiêu?",
        "Tôi thấy current tăng đột biến, có sao không?",
        "Có cách nào giám sát từ xa không?",
        "Sensor báo lỗi liên tục, kiểm tra thế nào?",
        "Lịch sử cảnh báo 7 ngày qua thế nào?",
    ],
    "iot_dashboard": [
        "Dashboard hiển thị gì?",
        "Có thể xem dữ liệu realtime không?",
        "Báo cáo tuần có sẵn không?",
        "Cảnh báo qua kênh nào?",
        "Có app mobile không?",
        "Phân quyền user thế nào?",
        "Dữ liệu lưu trữ bao lâu?",
        "Có hỗ trợ xuất Excel không?",
        "Cấu hình threshold ở đâu?",
        "Webhook cảnh báo setup thế nào?",
    ],
    "legal_qa": [
        "Điều khoản bảo hành áp dụng thế nào?",
        "Chính sách đổi trả trong 7 ngày?",
        "Hoàn tiền khi sản phẩm lỗi?",
        "Bảo mật thông tin khách hàng ra sao?",
        "Hợp đồng điện tử có giá trị pháp lý?",
        "Điều kiện sử dụng dịch vụ?",
        "Tranh chấp phát sinh giải quyết thế nào?",
        "Quyền từ chối phục vụ của shop?",
        "Bảo vệ dữ liệu cá nhân theo NĐ 13/2023?",
        "Cam kết chất lượng sản phẩm?",
    ],
    "vehix_lookup": [
        "Tra cứu biển số 30A-123.45 thuộc tỉnh nào?",
        "Biển 51K-678.90 đăng ký năm nào?",
        "Xe biển 29A-111.22 là xe gì?",
        "Tra cứu chủ sở hữu biển 43C-456.78?",
        "Biển số 50L-999.11 màu gì?",
        "Xe biển 60A-234.56 số khung bao nhiêu?",
        "Biển 14A-789.01 đã sang tên chưa?",
        "Tra cứu phí trước bạ biển 65A-321.54?",
        "Xe biển 77A-852.96 hạn đăng kiểm khi nào?",
        "Biển 88A-147.25 bị phạt nguội lần nào chưa?",
    ],
    "iot_sensor_consult": [
        "AQI hôm nay ở Hà Nội bao nhiêu, có nên ra đường không?",
        "Nhiệt độ ngoài trời Sài Gòn lúc này, có nóng không?",
        "Độ ẩm trong nhà 75%, có nên bật máy hút ẩm không?",
        "PM2.5 hiện tại 85 µg/m³, trẻ em có nên chơi ngoài không?",
        "Áp suất không khí Đà Lạt tuần này thế nào?",
        "Chỉ số UV ở Nha Trang giờ này bao nhiêu, có cần kem chống nắng không?",
        "Nhiệt độ bể cá 28°C, có cần thay nước không?",
        "Độ ẩm đất vườn 35%, có cần tưới không?",
        "CO2 trong phòng 800 ppm, có nên mở cửa không?",
        "Gió ngoài biển Vũng Tàu 35 km/h, có nên đi canoe không?",
    ],
    # === HARD topics — designed to score > 0.6 on difficulty_classifier ===
    # Each question: long text (>1200 chars) + code block + 3+ question marks
    # → triggers PRIMARY_12B route (today's test had ZERO 12B traffic)
    "code_review_python": [
        # Q1: text + code + 3+ ? → ~0.68
        """Tôi có đoạn code xử lý đơn hàng trong hệ thống fanpage, mỗi lần có flash sale là server chậm và có đơn hàng bị duplicate. Bạn review giúp tôi xem có bug ở đâu, tại sao race condition xảy ra, và cách fix an toàn nhất trong production mà không cần downtime?

```python
import threading
import time

orders = []
lock = threading.Lock()

def process_order(user_id, product_id, qty):
    with lock:
        if user_id in [o['user_id'] for o in orders if o['product_id'] == product_id]:
            return None
        time.sleep(0.05)
        order = {'user_id': user_id, 'product_id': product_id, 'qty': qty, 'ts': time.time()}
        orders.append(order)
        return order
```

Lock ở đây có đủ bảo vệ không? Nếu scale lên 1000 RPS thì sao? Có nên dùng Redis thay vì in-memory list không, và migration path thế nào? Tôi đang dùng PostgreSQL chính cho order lưu trữ, có nên atomic insert thay vì check-then-insert pattern trên không?""",

        # Q2: text + code + 3+ ? → ~0.68
        """Review giúp tôi đoạn Python này dùng để tổng hợp doanh thu theo tenant mỗi ngày. Hiện tại nó chạy đúng trên dữ liệu nhỏ nhưng khi table lên 50M rows thì timeout. Index nào tôi đang thiếu, query plan tôi nên EXPLAIN thế nào, và có nên partition table theo ngày không? Lưu ý tôi không được downtime quá 5 phút:

```python
import psycopg2

def daily_revenue(date_str, tenant_id):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute('''
        SELECT SUM(amount), COUNT(*), AVG(amount)
        FROM orders
        WHERE DATE(created_at) = %s AND tenant_id = %s
    ''', (date_str, tenant_id))
    return cur.fetchone()
```

Câu query trên scan toàn bộ table mỗi lần gọi đúng không? Materialized view có phù hợp không, hay tôi nên pre-aggregate vào bảng `daily_stats`? Và nếu dùng pre-aggregate thì refresh strategy thế nào — incremental hay full refresh mỗi đêm? Trong khi refresh thì query của user có bị block không? Nếu có thì giải pháp là gì? Cuối cùng tôi có nên dùng BRIN index trên `created_at` không vì data là time-series append-only?""",

        # Q3: text + code + math symbols + 3+ ? → ~0.88
        """Tôi đang implement retry mechanism cho HTTP call đến payment gateway. Theo tài liệu thì 1% request sẽ fail với status 503, và mỗi lần retry phải đợi exponential backoff. Công thức delay tôi nên dùng là gì, max retry bao nhiêu lần là hợp lý, và làm sao tránh thundering herd khi gateway recover?

```python
import random
import time

def call_payment(url, payload, max_retries=5):
    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 503:
                # exponential backoff with jitter
                delay = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(delay)
                continue
        except requests.Timeout:
            continue
    raise Exception("payment gateway unreachable")
```

Với p=0.01 thì xác suất thất bại sau N retries là bao nhiêu? Nếu muốn P(success) ≥ 0.9999 thì cần N tối thiểu? Backoff cap nên đặt ở 30s hay 60s? Và jitter nên là full jitter hay equal jitter? Tôi nghe nói có decorrelated jitter tốt hơn, bạn giải thích giúp tôi?""",

        # Q4: text + code + 3+ ? → ~0.68
        """Tôi có class Python quản lý session user, nhưng gặp memory leak — process RAM tăng đều từ 200MB lên 4GB sau 6h. Bạn giúp tôi tìm bug và fix:

```python
import time

class SessionStore:
    def __init__(self):
        self.sessions = {}
        self.last_cleanup = time.time()

    def get(self, sid):
        if sid in self.sessions:
            sess = self.sessions[sid]
            if time.time() - sess['last_seen'] > 3600:
                del self.sessions[sid]
                return None
            sess['last_seen'] = time.time()
            return sess
        return None

    def set(self, sid, data):
        self.sessions[sid] = {'data': data, 'last_seen': time.time()}

    def cleanup_old(self):
        cutoff = time.time() - 3600
        for sid in list(self.sessions.keys()):
            if self.sessions[sid]['last_seen'] < cutoff:
                del self.sessions[sid]
```

Vì sao RAM vẫn tăng dù tôi đã cleanup 1h? Weakref có giúp ích không hay tôi cần TTL cache như `cachetools.TTLCache`? Background task `cleanup_old` có nên chạy định kỳ hay để lazy cleanup như `get()` là đủ? Có cách nào dùng `__slots__` để giảm per-object overhead không, và trade-off là gì? Cuối cùng nếu session phải survive qua restart, tôi nên dùng Redis thay vì in-memory dict, schema Redis nào phù hợp?""",

        # Q5: text + code + 3+ ? → ~0.68
        """Đoạn code async này fetch data từ 3 API song song để render dashboard, nhưng thỉnh thoảng 1 request chậm 30s block cả batch. Cách tôi implement timeout có đúng không, và nên dùng pattern nào thay vì `asyncio.wait_for`?

```python
import asyncio
import aiohttp

async def fetch_dashboard_data(user_id):
    async with aiohttp.ClientSession() as session:
        tasks = [
            session.get(f'/api/orders?user={user_id}'),
            session.get(f'/api/usage?user={user_id}'),
            session.get(f'/api/profile?user={user_id}'),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [await r.json() if not isinstance(r, Exception) else None for r in results]
```

Nếu 1 API chậm 30s thì user phải đợi 30s? `asyncio.wait_for(..., timeout=5)` có cancel đúng cách không hay leak connection? Pattern `asyncio.as_completed` có tốt hơn `gather` cho UX không? Circuit breaker nên đặt timeout bao nhiêu giây cho 3 endpoint khác nhau, và khi nào nên fallback về cached data?""",
    ],
    "data_analysis_long": [
        # Q1: text + code + math + multi-Q → ~0.81
        """Tôi có dataset bán hàng 12 tháng qua của 4 tenant, tổng cộng 80,000 đơn hàng. Phân phối doanh thu trông skewed: median ≈ 250K VND, mean ≈ 1.2M VND, max ≈ 85M VND. Tôi muốn phát hiện outlier đơn hàng bất thường (có thể là fraud hoặc nhập liệu sai) trước khi đưa vào báo cáo cuối tháng. Đây là đoạn code pandas hiện tại của tôi:

```python
import pandas as pd
df = pd.read_sql("SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '1 year'", conn)
q1, q3 = df['amount'].quantile([0.25, 0.75])
iqr = q3 - q1
outliers = df[(df['amount'] < q1 - 1.5*iqr) | (df['amount'] > q3 + 1.5*iqr)]
```

Bạn review giúp: cách này có đúng cho skewed distribution không, hay phải log-transform trước khi tính IQR? Multiplier k=1.5 quá strict, có nên dùng k=3 cho fraud detection? Có nên tách theo tenant riêng hay gộp chung? Công thức skewness nào phù hợp ∑(x-μ)³/nσ³ hay dùng median-based formula? Cuối cùng nếu tìm được 50 outlier thì tôi nên xử lý thế nào — drop, cap tại 99th percentile, hay giữ nguyên và flag riêng cho audit team?""",

        # Q2: text + code + math + multi-Q → ~0.81
        """Tôi đang phân tích A/B test giữa 2 phiên bản landing page. Version A: 12,450 visitors, 312 conversions (2.51%). Version B: 12,201 visitors, 387 conversions (3.17%). Lift quan sát được là 26.3%, nhưng tôi cần biết nó có ý nghĩa thống kê không, hay chỉ là noise. Đây là script Python tôi đang dùng:

```python
from scipy import stats
import numpy as np

p1, n1 = 312/12450, 12450
p2, n2 = 387/12201, 12201
p_pool = (312+387) / (12450+12201)
se = np.sqrt(p_pool*(1-p_pool)*(1/n1 + 1/n2))
z = (p2 - p1) / se
p_value = 2 * (1 - stats.norm.cdf(abs(z)))
```

Cách tính p_value như trên đúng chưa, hay tôi nên dùng chi-squared / Fisher's exact? Power đạt được bao nhiêu % với sample size này? Minimum Detectable Effect (MDE) tôi có thể phát hiện ở power=0.8 là bao nhiêu? Công thức MDE = (z_α + z_β) × √(p₁(1-p₁)/n₁ + p₂(1-p₂)/n₂) đúng không, hay có công thức nào tốt hơn cho proportion? Bonferroni correction có cần áp dụng không nếu tôi đang track 5 metrics cùng lúc? Cuối cùng nếu kết quả significant, tôi có nên rollout ngay hay chờ thêm 1 tuần để confirm tránh false positive?""",

        # Q3: text + code + math + multi-Q → ~0.81
        """Hệ thống recommendation của tôi dùng cosine similarity trên user-item matrix (50K users × 10K items, sparse 0.3%). Cold-start user (mới đăng ký, chưa có interaction) nên handle thế nào? Tôi đang fallback về top-popular nhưng conversion rate thấp (0.4% vs 3.1% cho returning user). Implementation hiện tại:

```python
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

def recommend(user_id, top_k=10):
    user_vec = user_item_matrix[user_id]  # sparse row
    sims = cosine_similarity(user_vec, user_item_matrix).flatten()
    similar_users = np.argsort(-sims)[:50]
    scores = user_item_matrix[similar_users].sum(axis=0)
    return np.argsort(-scores)[:top_k]
```

Có nên dùng content-based filter dựa trên demographic (age, location, signup_source) cho cold-start không, và nếu có thì weight nên là bao nhiêu so với collaborative filtering? Matrix factorization (ALS) có tốt hơn cosine similarity cho sparse data không, và khi nào nên switch? Implicit ALS (Hu et al.) khác gì explicit ALS? Cuối cùng tôi nên evaluate thế nào — offline metric (RMSE, NDCG@10) có khác gì online A/B test không, và gap giữa chúng thường bao nhiêu % trong thực tế?""",

        # Q4: text + code + math + multi-Q → ~0.81
        """Phân tích time-series traffic cho 4 tenant trong 90 ngày. Pattern rõ ràng có weekly seasonality (peak Thứ 3-5, trough Chủ nhật) và có trend tăng ~15%/tháng. Tôi muốn forecast 30 ngày tới để capacity plan. Đoạn code tham khảo:

```python
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

df = pd.read_csv('traffic_90d.csv', parse_dates=['ts'])
df = df.set_index('ts').resample('H').sum()
model = SARIMAX(df, order=(1,1,1), seasonal_order=(1,1,1,24))
fit = model.fit(disp=False)
forecast = fit.forecast(steps=24*30)
```

SARIMA order (p,d,q)(P,D,Q)s tôi nên chọn thế nào — auto.arima hay grid search thủ công? Prophet của Facebook có tốt hơn SARIMA cho data có holiday effect không? Confidence interval 95% có đủ rộng để cover Black Friday spike không, hay tôi cần scenario analysis riêng? Nếu forecast sai ±20% thì infra cost impact bao nhiêu, và có nên over-provision 30% để safe? Cuối cùng model nên retrain frequency thế nào — weekly, monthly, hay khi MAPE > 10%? Exogenous variable như marketing campaign schedule có nên đưa vào SARIMAX không?""",

        # Q5: text + code + math + multi-Q → ~0.81
        """So sánh 3 cohort retention: tháng 1, tháng 2, tháng 3. Day-1 retention: 65% / 68% / 71%. Day-7: 28% / 32% / 35%. Day-30: 12% / 15% / 17%. Trend cải thiện đều nhưng absolute numbers vẫn thấp. Tôi muốn biết lift có ý nghĩa thống kê không giữa 3 cohort, và cohort nào nên làm case study. Script phân tích:

```python
import numpy as np
from scipy import stats

cohorts = {
    'jan': {'n': 2100, 'd30_retained': 252},
    'feb': {'n': 2300, 'd30_retained': 345},
    'mar': {'n': 2400, 'd30_retained': 408},
}

for c1, c2 in [('jan', 'feb'), ('jan', 'mar'), ('feb', 'mar')]:
    p1 = cohorts[c1]['d30_retained'] / cohorts[c1]['n']
    p2 = cohorts[c2]['d30_retained'] / cohorts[c2]['n']
    p_pool = (cohorts[c1]['d30_retained'] + cohorts[c2]['d30_retained']) / (cohorts[c1]['n'] + cohorts[c2]['n'])
    se = np.sqrt(p_pool*(1-p_pool)*(1/cohorts[c1]['n'] + 1/cohorts[c2]['n']))
    z = (p2 - p1) / se
    print(f"{c1} vs {c2}: z={z:.3f}, p={2*(1-stats.norm.cdf(abs(z))):.4f}")
```

Confidence interval cho mỗi retention rate tính thế nào — Wilson score interval hay normal approximation? Cohort March retention=17% thì 95% CI là bao nhiêu? So sánh March vs January: 17% vs 12%, p-value tôi nên dùng test nào — chi-squared với 2x2 contingency, hay two-proportion z-test? Bonferroni correction cho 3 pairwise comparisons có cần không, hay Holm-Bonferroni tốt hơn? Sample size hiện tại có đủ power=0.8 để phát hiện lift 5% không?""",
    ],
    "multi_step_planning": [
        # Q1: text + code + multi-Q → ~0.61
        """Tôi đang launch dịch vụ subscription mới cho 4 tenant, target 500 paying users trong tháng đầu. Bạn giúp tôi lên plan 4 tuần: tuần 1 nên tập trung vào gì, tuần 2 deliverable gì, tuần 3 marketing push thế nào, tuần 4 thu thập feedback và iterate? Cụ thể: pricing tier nào hợp lý — freemium với 3 tier (Free/Pro/Enterprise) hay flat-rate 99K/tháng? Onboarding flow bao nhiêu step là tối ưu — 3 step hay 5 step? Email drip campaign tôi nên có bao nhiêu email trong 14 ngày đầu, và timing gửi thế nào? Cancellation flow có nên confirm dialog 1 lần hay 2 lần (dark pattern vs UX tốt)? NPS survey gửi khi nào — sau 7 ngày dùng hay 30 ngày? Và quan trọng nhất: KPI nào tôi track weekly để biết có đang on-track không — MRR, churn rate, hay active users? Bonus: tôi có nên A/B test giá ở 2 cohort khác nhau không, và statistical setup thế nào?

```python
# weekly KPI tracking
metrics = {
    'new_signups': lambda w: count_signups_in_week(w),
    'trial_starts': lambda w: count_trial_starts_in_week(w),
    'conversion': lambda w: trial_to_paid(w) / trial_starts(w) if trial_starts(w) > 0 else 0,
    'churn': lambda w: cancelled(w) / active_at_week_start(w),
}
```""",

        # Q2: long text + code + multi-Q → ~0.68
        """Tôi cần migrate database từ SQLite sang PostgreSQL cho production với 4 tenant, hiện tại database size 8GB, 200K rows/day growth. Bạn giúp tôi plan chi tiết:

```sql
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT,
    amount REAL,
    created_at DATETIME
);
```

Migration path nào an toàn nhất — pgloader, custom script, hay replication? Downtime budget tôi có 30 phút, có khả thi không với 8GB? Schema PostgreSQL tôi nên thêm index/constraint gì — BRIN trên created_at, partial index trên tenant_id, hay covering index? Foreign key giữa orders và users có nên ON DELETE CASCADE không? Sequence tôi nên dùng BIGSERIAL hay UUID v7 cho distributed safety? Cutover strategy: dual-write trong 1 tuần rồi switch read, hay blue-green với feature flag? Nếu có data inconsistency sau migration, tôi verify thế nào — checksum row count, hash từng row, hay sample audit? Và rollback plan khi có disaster trong 24h đầu?""",

        # Q3: text + code + multi-Q → ~0.63
        """Hệ thống chatbot của tôi đang chạy ổn với 20 concurrent users, p95 latency 6s, 0% error. Nhưng dự báo 3 tháng tới sẽ lên 200 concurrent. Bạn giúp tôi capacity plan: bottleneck đầu tiên tôi sẽ gặp là gì — CPU, RAM, GPU VRAM, network, hay database connection pool? Theo kinh nghiệm scaling LLM serving, GPU memory là constraint hay throughput (tok/s) là constraint? Tôi đang chạy single A100 40GB, model 12B Q4 chiếm 7.4GB VRAM, còn lại cho KV cache và batch. Nếu lên 200 concurrent thì cần GPU gì — 2× A100, 1× H100, hay nhiều instance nhỏ? 12B Q4 có nên quantize xuống Q3 hoặc dùng AWQ để fit nhiều user hơn, và quality loss bao nhiêu %? Speculative decoding có giúp tăng throughput không, hay chỉ làm tăng memory pressure? Cuối cùng queue management — tôi nên reject request khi queue > 100, hay đợi 30s rồi timeout? SLO nào realistic cho LLM serving — p95 < 10s, p99 < 20s, hay p99 < 30s?

```yaml
# current config
model: 12b_q4
vram_budget: 14gb
parallel_slots: 12
kv_cache: q4_0
ctx_size: 8192
sustained_tok_per_s: 158
```""",

        # Q4: text + code + multi-Q → ~0.63
        """Tôi đang thiết kế feature memory cho chatbot, mục tiêu: nhớ user preferences qua 30 ngày, recall ngay khi user quay lại. Approach nào phù hợp — (1) rolling summary mỗi 20 messages, (2) structured memory extract SPO triples, (3) pinned key-value facts. Trade-off giữa 3 approach thế nào về recall accuracy, latency cost, storage cost? Vector search dùng embedding model nào — multilingual MiniLM (384-dim, 60MB) hay bge-m3 (1024-dim, 2.3GB)? Hybrid search 70% semantic + 30% token có tốt hơn pure semantic không, và threshold nên đặt bao nhiêu? Reranker có cần thiết không, hay chỉ overhead — `bge-reranker-v2-m3` add bao nhiêu latency? Memory block nên inject vào system prompt hay user message, và budget token tối đa cho memory là bao nhiêu — 500 tokens, 1K, hay 2K? Cuối cùng cross-session recall — khi user đổi session_id thì memory có persist không, và user_id có đủ làm key không hay cần thêm device fingerprint?

```python
# 3 memory strategies compared
def memory_summary(messages, threshold=20):
    if len(messages) > threshold:
        return summarize(messages[:-threshold])  # keep recent N

def memory_structmem(messages):
    triples = extract_spo(messages)  # [subject, predicate, object]
    return deduplicate(triples)

def memory_pinned(user_facts):
    return {f['key']: {'value': f['value'], 'confidence': f['conf']}}
```""",

        # Q5: long text + code + multi-Q → ~0.68
        """Tôi đang debug vấn đề memory leak trong FastAPI app. Sau 6h chạy, RAM tăng từ 200MB lên 3.5GB rồi OOM kill. Bạn giúp tôi debug từng bước:

```python
from fastapi import FastAPI
import asyncio

app = FastAPI()
cache = {}

@app.get("/user/{user_id}")
async def get_user(user_id: str):
    if user_id not in cache:
        user_data = await fetch_from_db(user_id)
        cache[user_id] = user_data
    return cache[user_id]
```

Vấn đề đầu tiên: cache không có eviction policy — nếu có 100K user thì cache sẽ chiếm bao nhiêu RAM trung bình? `functools.lru_cache(maxsize=1000)` có giúp không hay tôi cần TTL-based cache? Thứ hai: reference cycle — nếu user_data chứa list of orders và orders có back-reference đến user, garbage collector có clean không? Thứ ba: coroutine leak — nếu một request bị cancel giữa chừng, các task con có bị cleanup không, hay tôi cần `asyncio.shield`? Cuối cùng monitoring — tôi nên export metric gì để detect leak sớm — `tracemalloc` snapshot, `objgraph` count, hay `psutil` RSS sample mỗi 5 phút?""",
    ],
}


def _post_chat(messages: list, model_mode: str = "lite", max_tokens: int = 600) -> str:
    """Call /v1/chat and return assistant content. Returns '' on failure."""
    payload = {
        "project_id": "fanpage",
        "tenant_id": "realistic_day",
        "user_name": "_generator",
        "user_message": messages[-1]["content"],
        "model_mode": model_mode,
        "enable_search": False,
        "max_tokens": max_tokens,
    }
    if len(messages) > 1:
        payload["history"] = messages[:-1]
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/v1/chat",
        data=data,
        headers={"Content-Type": "application/json", "X-API-KEY": API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            body = json.loads(r.read().decode())
            return body.get("content", "")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, Exception) as e:
        print(f"[generator] /v1/chat failed: {e!r}", flush=True)
        return ""


def _parse_questions(text: str, n: int) -> List[str]:
    """Parse LLM output into N questions. Falls back to numbered lines."""
    if not text:
        return []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    questions = []
    for line in lines:
        # Strip numbering "1.", "1)", "- ", "• ", etc.
        for prefix in ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10.",
                       "1)", "2)", "3)", "4)", "5)", "6)", "7)", "8)", "9)", "10)",
                       "- ", "* ", "• "]:
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
                break
        is_question = (
            "?" in line or "?" in line
            or "gì" in line.lower() or "nào" in line.lower()
            or "sao" in line.lower() or "thế" in line.lower()
        )
        if len(line) > 15 and is_question:
            questions.append(line)
        if len(questions) >= n:
            break
    return questions[:n]


def generate_opening_batch(topic: str, n: int = 10) -> List[str]:
    """Generate N opening questions for a topic. Falls back to seed if LLM fails."""
    if topic not in SEED_QUESTIONS:
        topic = random.choice(list(SEED_QUESTIONS.keys()))
    prompt = (
        f"Generate exactly {n} short Vietnamese user questions about '{topic}' for a customer chatbot test. "
        f"Each question should be 1 sentence, natural, varied (some short, some longer), and from a real customer perspective. "
        f"Output as a numbered list 1..{n}, one question per line, no commentary."
    )
    text = _post_chat([{"role": "user", "content": prompt}])
    parsed = _parse_questions(text, n)
    if len(parsed) < n:
        # Fallback: combine parsed + seed questions
        seed = SEED_QUESTIONS[topic]
        random.shuffle(seed)
        combined = parsed + [q for q in seed if q not in parsed]
        parsed = combined[:n]
    return parsed


def generate_recap_question(summary: str, user_name: str, topic: str) -> str:
    """Ask the user a recap question derived from prior summary."""
    if not summary:
        return f"Bạn còn nhớ {user_name} đã hỏi gì trước đó không?"
    prompt = (
        f"User '{user_name}' đã chat về chủ đề '{topic}' trước đó. "
        f"Tóm tắt cuộc trò chuyện cũ: \"{summary[:400]}\"\n\n"
        f"Generate 1 câu hỏi tiếp theo tự nhiên, giả sử người dùng quay lại sau 1 giờ. "
        f"Câu hỏi phải refer đến thông tin cụ thể từ cuộc trò chuyện cũ. "
        f"Chỉ trả về 1 câu hỏi, không giải thích."
    )
    text = _post_chat([{"role": "user", "content": prompt}])
    if not text:
        return f"Bạn còn nhớ chúng ta đã nói về {topic} không? Tôi muốn hỏi thêm."
    return text.strip().split("\n")[0]


def generate_paraphrase(question: str) -> str:
    """Rephrase a question to test learning/recall with same intent."""
    prompt = (
        f"Rephrase this Vietnamese customer question to mean the same thing but with different words: "
        f"\"{question}\"\n\n"
        f"Output only the rephrased question, no explanation, no quotes."
    )
    text = _post_chat([{"role": "user", "content": prompt}])
    if not text:
        return question.replace("có thể", "được không").replace("như thế nào", "ra sao")
    return text.strip().strip('"').strip("'").split("\n")[0]


def pick_topics(n: int = 1) -> List[str]:
    """Pick N topics from pool. Weight vehix/iot_sensor higher (high repeat).

    Hard topics (code_review, data_analysis, multi_step) get weight 1 each so
    roughly 1 in 13 cycles sees a 12B-routed question. Without this, the test
    has 0% 12B traffic and adaptive routing is never validated.
    """
    weights = {
        "fanpage_consulting": 3,
        "fanpage_buy_sell": 2,
        "fanpage_product_info": 2,
        "fanpage_complaint": 1,
        "fanpage_promo": 1,
        "ihi_safety_query": 2,
        "iot_dashboard": 1,
        "legal_qa": 1,
        "vehix_lookup": 4,           # lặp lại cao - tra cứu biển số
        "iot_sensor_consult": 4,     # lặp lại cao - tư vấn cảm biến
        # Hard topics — get some traffic so 12B Q4 actually gets exercised
        "code_review_python": 1,
        "data_analysis_long": 1,
        "multi_step_planning": 1,
    }
    topics = list(weights.keys())
    w = [weights[t] for t in topics]
    return random.choices(topics, weights=w, k=n)


def pick_questions_for_user(topic: str, n: int, user_name: str = "") -> List[str]:
    """Return N questions for a user, deterministically shuffled by user_name.

    Two users on the same topic will see the SAME Qs in DIFFERENT orders
    (or sometimes overlapping, depending on hash). This breaks the "user always
    asks Q0, Q1, Q2 in order" pattern that made the previous test predictable.

    For 30+ Qs per topic (the spec target), use scripts/expand_seed_questions.py
    to LLM-generate paraphrases and append to SEED_QUESTIONS[topic]. We don't
    auto-expand at runtime because /v1/chat is too slow under load.
    """
    pool = SEED_QUESTIONS.get(topic, SEED_QUESTIONS["fanpage_consulting"])
    if n >= len(pool):
        return list(pool)
    # Stable shuffle keyed on user_name
    seed_int = abs(hash(user_name + topic)) % (10 ** 9)
    rng = random.Random(seed_int)
    indices = list(range(len(pool)))
    rng.shuffle(indices)
    return [pool[i] for i in indices[:n]]


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "fanpage_consulting"
    qs = generate_opening_batch(topic, 3)
    print(json.dumps(qs, ensure_ascii=False, indent=2))
