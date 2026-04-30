#!/usr/bin/env python3
"""
Memory Stress Test — 10 concurrent users × 50 questions each.

Mỗi user hỏi về 1 chủ đề riêng biệt (49 câu khác nhau trong chủ đề đó).
Câu 50 là câu CHỐT: "Hãy tóm tắt lại tất cả những gì tôi đã hỏi bạn trong
cuộc trò chuyện này, bạn có nhớ không?"

Kiểm tra:
  - Khả năng chịu tải concurrent 10 users;
  - Memory recall: chatbot có nhớ được nội dung 40+ câu trước không;
  - Session isolation: user này không thấy context user khác;
  - Latency statistics per round.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = os.getenv("AIHUB_LOADTEST_URL", "http://127.0.0.1:8000")
PROJECT_ID = os.getenv("AIHUB_LOADTEST_PROJECT", "test")
TENANT_ID = os.getenv("AIHUB_LOADTEST_TENANT", "memstress")
TIMEOUT_SECONDS = float(os.getenv("AIHUB_LOADTEST_TIMEOUT", "120"))
MODEL_MODE = os.getenv("AIHUB_LOADTEST_MODEL_MODE", "lite")
PROVIDER = os.getenv("AIHUB_LOADTEST_PROVIDER", "")
ALLOW_EXTERNAL = os.getenv("AIHUB_LOADTEST_ALLOW_EXTERNAL", "false").lower() == "true"
REPORT_NAME = os.getenv("AIHUB_LOADTEST_REPORT", "memory_stress_report.json")
USE_SENTINELS = os.getenv("AIHUB_LOADTEST_SENTINELS", "false").lower() == "true"
SENTINEL_TURNS = (1, 10, 20, 30, 40)
NUM_QUESTIONS = max(2, int(os.getenv("AIHUB_LOADTEST_QUESTIONS", "50")))
USER_COUNT = max(1, int(os.getenv("AIHUB_LOADTEST_USERS", "10")))
MAX_CONCURRENCY = max(0, int(os.getenv("AIHUB_LOADTEST_MAX_CONCURRENCY", "0")))
USER_DELAY_SECONDS = max(0.0, float(os.getenv("AIHUB_LOADTEST_USER_DELAY_SECONDS", "0")))
PREVIEW_CHARS = max(1, int(os.getenv("AIHUB_LOADTEST_PREVIEW_CHARS", "200")))
ANSWER_STYLE = os.getenv("AIHUB_LOADTEST_ANSWER_STYLE", "normal").lower()
BRIEF_ANSWER_MAX_CHARS = max(80, int(os.getenv("AIHUB_LOADTEST_BRIEF_MAX_CHARS", "500")))

# ── Topic bank: 10 distinct topics (1 per user) ───────────────────────────────
# Each topic has 49 unique questions + 1 final memory-check question (Q50)
TOPICS: list[dict] = [
    {
        "name": "Python Programming",
        "user": "stress_user_01",
        "questions": [
            "Python được tạo ra năm nào và bởi ai?",
            "Sự khác biệt giữa list và tuple trong Python là gì?",
            "Dictionary comprehension trong Python dùng như thế nào?",
            "GIL trong Python là gì? Nó ảnh hưởng đến multi-threading như nào?",
            "asyncio trong Python dùng để làm gì?",
            "Decorator trong Python là gì? Cho ví dụ đơn giản.",
            "Generator vs Iterator khác nhau thế nào?",
            "Context manager (`with` statement) hoạt động ra sao?",
            "Type hints trong Python 3.x có tác dụng gì?",
            "Cách dùng dataclass trong Python?",
            "virtualenv vs conda khác nhau thế nào?",
            "pip và poetry dùng để làm gì?",
            "__init__.py có vai trò gì trong package?",
            "Cách xử lý exception trong Python đúng cách?",
            "Walrus operator (:=) dùng khi nào?",
            "f-string có ưu điểm gì so với format()?",
            "Cách sort list of dict theo một key?",
            "zip() và enumerate() dùng khi nào?",
            "lambda function có hạn chế gì?",
            "map(), filter(), reduce() - khi nào nên dùng?",
            "Python có pass-by-value hay pass-by-reference?",
            "Cách đọc/ghi file CSV trong Python?",
            "json module dùng như thế nào?",
            "pathlib vs os.path khác nhau thế nào?",
            "unittest vs pytest - bạn ưu tiên cái nào và tại sao?",
            "mock trong unit test là gì?",
            "logging module dùng như thế nào?",
            "argparse dùng để làm gì?",
            "Cách cài đặt môi trường Python trên Linux?",
            "sys.argv là gì?",
            "Cách đọc biến môi trường trong Python?",
            "subprocess module dùng khi nào?",
            "threading vs multiprocessing khi nào dùng cái nào?",
            "concurrent.futures.ThreadPoolExecutor dùng thế nào?",
            "asyncio.gather() vs asyncio.create_task() khác nhau thế nào?",
            "Cách viết REST API đơn giản với FastAPI?",
            "Pydantic dùng để làm gì?",
            "SQLAlchemy ORM là gì?",
            "requests vs httpx khác nhau thế nào?",
            "Cách debug Python code hiệu quả?",
            "pdb debugger dùng thế nào?",
            "Cách profiling code Python?",
            "memory_profiler dùng để làm gì?",
            "numpy array khác list Python như thế nào?",
            "pandas DataFrame cơ bản là gì?",
            "Cách xử lý NaN trong pandas?",
            "matplotlib dùng để làm gì?",
            "Cách vẽ biểu đồ đơn giản với matplotlib?",
            "scikit-learn dùng để làm gì trong machine learning?",
        ],
    },
    {
        "name": "Vietnamese History",
        "user": "stress_user_02",
        "questions": [
            "Nước Văn Lang được thành lập năm nào?",
            "Hai Bà Trưng khởi nghĩa chống ai và vào năm nào?",
            "Lý Thường Kiệt nổi tiếng với trận đánh nào?",
            "Trần Hưng Đạo đánh bại quân Nguyên Mông mấy lần?",
            "Lê Lợi khởi nghĩa Lam Sơn bắt đầu năm nào?",
            "Nguyễn Huệ (Quang Trung) nổi tiếng với trận đánh nào?",
            "Triều Nguyễn bắt đầu từ năm nào?",
            "Pháp xâm lược Việt Nam bắt đầu từ đâu?",
            "Phong trào Đông Du do ai khởi xướng?",
            "Cách mạng tháng Tám 1945 diễn ra vào ngày nào?",
            "Hồ Chí Minh đọc Tuyên ngôn Độc lập tại đâu?",
            "Chiến dịch Điện Biên Phủ kết thúc năm nào?",
            "Hiệp định Geneva 1954 quy định gì?",
            "Chiến tranh Việt Nam kết thúc vào ngày nào?",
            "Thành phố Hồ Chí Minh trước đây tên là gì?",
            "Chính sách Đổi Mới bắt đầu năm nào?",
            "Việt Nam gia nhập ASEAN năm nào?",
            "Việt Nam gia nhập WTO năm nào?",
            "Thủ đô Việt Nam là thành phố nào?",
            "Dân số Việt Nam hiện tại khoảng bao nhiêu?",
            "Việt Nam có bao nhiêu tỉnh thành?",
            "Đồng bằng sông Cửu Long có đặc điểm gì?",
            "Vịnh Hạ Long thuộc tỉnh nào?",
            "Cố đô Huế nổi tiếng với gì?",
            "Phố cổ Hội An được UNESCO công nhận năm nào?",
            "Thánh địa Mỹ Sơn là di tích của văn hóa nào?",
            "Trống đồng Đông Sơn có nguồn gốc từ đâu?",
            "Chữ Nôm là gì?",
            "Chữ Quốc ngữ được phát triển bởi ai?",
            "Áo dài xuất hiện vào thời kỳ nào?",
            "Tết Nguyên Đán diễn ra vào khoảng thời gian nào?",
            "Lễ hội Chùa Hương nổi tiếng ở đâu?",
            "Phở có nguồn gốc từ đâu?",
            "Bánh mì Việt Nam nổi tiếng thế giới như thế nào?",
            "Cà phê trứng là đặc sản của thành phố nào?",
            "Dân tộc Kinh chiếm bao nhiêu % dân số?",
            "Việt Nam có bao nhiêu dân tộc?",
            "Tiếng Việt thuộc ngữ hệ nào?",
            "Nền kinh tế Việt Nam hiện đứng thứ mấy Đông Nam Á?",
            "Xuất khẩu chủ lực của Việt Nam là gì?",
            "Samsung đặt nhà máy lớn nhất ở tỉnh nào của Việt Nam?",
            "Vũ Văn Hiếu là ai?",
            "Ngô Quyền nổi tiếng với trận chiến nào?",
            "Đinh Bộ Lĩnh thống nhất đất nước vào thế kỷ mấy?",
            "Lý Công Uẩn dời đô từ đâu về đâu?",
            "Đại Việt Sử Ký Toàn Thư do ai biên soạn?",
            "Bình Ngô Đại Cáo là tác phẩm của ai?",
            "Truyện Kiều của Nguyễn Du viết về nhân vật nào?",
            "Nam quốc sơn hà được cho là của ai?",
            "Hịch Tướng Sĩ là tác phẩm của ai?",
        ],
    },
    {
        "name": "Machine Learning & AI",
        "user": "stress_user_03",
        "questions": [
            "Machine Learning là gì? Khác AI như thế nào?",
            "Supervised learning và unsupervised learning khác nhau thế nào?",
            "Reinforcement learning được dùng trong ứng dụng nào?",
            "Neural network hoạt động như thế nào cơ bản?",
            "Deep learning khác machine learning truyền thống ra sao?",
            "Gradient descent là gì?",
            "Learning rate ảnh hưởng đến training như thế nào?",
            "Overfitting là gì? Cách xử lý?",
            "Underfitting là gì? Khác overfitting thế nào?",
            "Regularization (L1, L2) dùng để làm gì?",
            "Dropout trong neural network là gì?",
            "Batch normalization dùng để làm gì?",
            "CNN (Convolutional Neural Network) dùng cho bài toán gì?",
            "RNN (Recurrent Neural Network) phù hợp với loại dữ liệu nào?",
            "LSTM giải quyết vấn đề gì của RNN?",
            "Transformer architecture là gì?",
            "Attention mechanism hoạt động ra sao?",
            "BERT là gì? Dùng cho bài toán nào?",
            "GPT khác BERT thế nào?",
            "Fine-tuning LLM là gì?",
            "LoRA (Low-Rank Adaptation) là gì?",
            "RAG (Retrieval Augmented Generation) là gì?",
            "Vector database dùng để làm gì trong AI?",
            "Embedding là gì trong NLP?",
            "Tokenization là gì?",
            "BPE (Byte Pair Encoding) là gì?",
            "Temperature trong LLM sampling là gì?",
            "Top-p và Top-k sampling khác nhau thế nào?",
            "Hallucination trong LLM là gì?",
            "Prompt engineering là gì?",
            "Chain-of-thought prompting là gì?",
            "Few-shot learning là gì?",
            "Zero-shot vs few-shot khác nhau thế nào?",
            "Model quantization là gì?",
            "GGUF format là gì?",
            "Ollama dùng để làm gì?",
            "vLLM khác Ollama thế nào?",
            "TensorFlow vs PyTorch - bạn ưu tiên cái nào?",
            "ONNX format dùng để làm gì?",
            "MLflow dùng để làm gì?",
            "Weights & Biases (wandb) là gì?",
            "Data augmentation là gì?",
            "Cross-validation là gì?",
            "Confusion matrix đọc như thế nào?",
            "Precision, Recall, F1-score là gì?",
            "ROC curve và AUC là gì?",
            "Mean Squared Error vs Mean Absolute Error?",
            "Feature engineering là gì?",
            "PCA (Principal Component Analysis) dùng để làm gì?",
            "t-SNE visualization dùng khi nào?",
        ],
    },
    {
        "name": "Web Development",
        "user": "stress_user_04",
        "questions": [
            "HTML, CSS, JavaScript khác nhau thế nào?",
            "DOM là gì?",
            "React.js là gì? Khác Angular và Vue thế nào?",
            "Virtual DOM trong React hoạt động thế nào?",
            "useState hook dùng để làm gì?",
            "useEffect hook dùng khi nào?",
            "Context API vs Redux - khi nào dùng cái nào?",
            "Next.js khác React thuần thế nào?",
            "SSR (Server Side Rendering) vs CSR (Client Side Rendering)?",
            "REST API là gì?",
            "GraphQL khác REST thế nào?",
            "HTTP methods: GET, POST, PUT, DELETE, PATCH khác nhau thế nào?",
            "Status codes HTTP: 200, 201, 400, 401, 403, 404, 500 là gì?",
            "CORS là gì? Tại sao cần nó?",
            "JWT (JSON Web Token) là gì?",
            "Cookie vs localStorage vs sessionStorage?",
            "HTTPS vs HTTP khác nhau thế nào?",
            "WebSocket dùng khi nào?",
            "Server-Sent Events (SSE) là gì?",
            "CSS Flexbox vs Grid - khi nào dùng cái nào?",
            "Responsive design là gì?",
            "Tailwind CSS là gì? Ưu điểm so với CSS thuần?",
            "TypeScript khác JavaScript thế nào?",
            "npm vs yarn vs pnpm khác nhau thế nào?",
            "webpack vs vite khác nhau thế nào?",
            "Docker dùng trong web dev như thế nào?",
            "CI/CD pipeline là gì?",
            "GitHub Actions dùng để làm gì?",
            "Nginx vs Apache khác nhau thế nào?",
            "Load balancer là gì?",
            "CDN (Content Delivery Network) là gì?",
            "Redis cache dùng trong web app thế nào?",
            "PostgreSQL vs MySQL khác nhau thế nào?",
            "ORM là gì? Cho ví dụ.",
            "SQL injection là gì? Cách phòng chống?",
            "XSS attack là gì?",
            "CSRF attack là gì?",
            "Rate limiting là gì? Tại sao cần?",
            "API gateway là gì?",
            "Microservices vs monolith khác nhau thế nào?",
            "Service mesh là gì?",
            "Kubernetes dùng để làm gì?",
            "Serverless architecture là gì?",
            "AWS Lambda là gì?",
            "PWA (Progressive Web App) là gì?",
            "Web performance: Core Web Vitals là gì?",
            "Lazy loading là gì?",
            "Code splitting trong React là gì?",
            "SEO cơ bản: meta tags nào quan trọng?",
            "Accessibility (a11y) trong web là gì?",
        ],
    },
    {
        "name": "Finance & Investment",
        "user": "stress_user_05",
        "questions": [
            "Cổ phiếu là gì? Khác trái phiếu thế nào?",
            "P/E ratio là gì? Đọc như thế nào?",
            "ROE (Return on Equity) là gì?",
            "EPS (Earnings Per Share) là gì?",
            "Vốn hóa thị trường tính như thế nào?",
            "VNINDEX là gì?",
            "Phân tích kỹ thuật và phân tích cơ bản khác nhau thế nào?",
            "RSI indicator dùng để làm gì?",
            "MACD indicator hoạt động thế nào?",
            "Bollinger Bands là gì?",
            "Candlestick chart đọc thế nào?",
            "Support và resistance trong trading là gì?",
            "Trend line vẽ như thế nào?",
            "Breakout trading là gì?",
            "Dollar Cost Averaging (DCA) là gì?",
            "Diversification (đa dạng hóa) tại sao quan trọng?",
            "Index fund là gì? Tại sao Buffett khuyên đầu tư vào đây?",
            "ETF khác mutual fund thế nào?",
            "Quỹ mở vs quỹ đóng khác nhau thế nào?",
            "Lãi suất ngân hàng ảnh hưởng đến thị trường chứng khoán thế nào?",
            "Lạm phát ảnh hưởng đến đầu tư thế nào?",
            "Gold có vai trò gì trong danh mục đầu tư?",
            "Bitcoin và crypto currency là gì?",
            "DeFi (Decentralized Finance) là gì?",
            "Blockchain hoạt động như thế nào?",
            "Margin trading là gì? Rủi ro gì?",
            "Stop loss và take profit là gì?",
            "Risk/Reward ratio là gì?",
            "Position sizing là gì?",
            "Backtesting chiến lược trading là gì?",
            "Quant trading là gì?",
            "HFT (High Frequency Trading) là gì?",
            "Market maker là gì?",
            "Liquidity trong thị trường là gì?",
            "Volume trading có ý nghĩa gì?",
            "IPO là gì? Cách tham gia IPO ở Việt Nam?",
            "Blue chip stocks là gì?",
            "Dividend yield tính như thế nào?",
            "Book value là gì?",
            "Free cash flow là gì?",
            "EBITDA là gì?",
            "Debt/Equity ratio cho ta biết gì?",
            "Current ratio là gì?",
            "Warren Buffett đầu tư theo triết lý gì?",
            "Peter Lynch nổi tiếng với chiến lược đầu tư nào?",
            "Benjamin Graham là ai? Đóng góp gì cho đầu tư?",
            "Thị trường gấu (bear market) và thị trường bò (bull market) là gì?",
            "Recession là gì? Ảnh hưởng đến đầu tư thế nào?",
            "Hedge fund khác private equity thế nào?",
            "Compound interest hoạt động thế nào? Tại sao quan trọng?",
        ],
    },
    {
        "name": "Health & Nutrition",
        "user": "stress_user_06",
        "questions": [
            "BMI là gì? Tính như thế nào?",
            "Protein, carbohydrate, fat có vai trò gì trong cơ thể?",
            "Calories là gì? Cần bao nhiêu calories/ngày?",
            "Vitamin C có tác dụng gì?",
            "Vitamin D thiếu dẫn đến bệnh gì?",
            "Sắt (Iron) thiếu gây ra vấn đề gì?",
            "Omega-3 fatty acid có lợi ích gì?",
            "Probiotics là gì? Tìm thấy trong thực phẩm nào?",
            "Chế độ ăn Mediterranean diet là gì?",
            "Keto diet hoạt động thế nào?",
            "Intermittent fasting là gì?",
            "Tại sao cần uống đủ nước mỗi ngày?",
            "Caffeine ảnh hưởng đến cơ thể thế nào?",
            "Đường (sugar) tiêu thụ quá nhiều gây hại gì?",
            "Muối (sodium) tiêu thụ bao nhiêu là vừa?",
            "Fiber (chất xơ) có tác dụng gì?",
            "Cholesterol: HDL và LDL khác nhau thế nào?",
            "Huyết áp bình thường là bao nhiêu?",
            "Tiểu đường type 1 và type 2 khác nhau thế nào?",
            "Béo phì ảnh hưởng đến sức khỏe thế nào?",
            "Tập thể dục bao nhiêu là đủ theo WHO?",
            "Aerobic exercise vs strength training - khác nhau thế nào?",
            "HIIT là gì?",
            "Yoga có lợi ích gì?",
            "Ngủ đủ giấc cần bao nhiêu tiếng?",
            "Thiếu ngủ ảnh hưởng đến cơ thể thế nào?",
            "Stress ảnh hưởng đến sức khỏe thế nào?",
            "Meditation có lợi ích gì?",
            "Mental health quan trọng thế nào?",
            "Depression và anxiety khác nhau thế nào?",
            "Vaccine hoạt động thế nào?",
            "Hệ miễn dịch hoạt động ra sao?",
            "Antibiotic resistance là gì? Tại sao nguy hiểm?",
            "Ung thư phát triển như thế nào?",
            "Phòng ngừa ung thư bằng lối sống thế nào?",
            "Tim mạch: yếu tố nguy cơ là gì?",
            "Đột quỵ (stroke) dấu hiệu nhận biết là gì?",
            "Nhồi máu cơ tim dấu hiệu cấp cứu là gì?",
            "Alzheimer bệnh là gì?",
            "Parkinson disease là gì?",
            "Cận thị có thể phòng ngừa không?",
            "Răng miệng ảnh hưởng đến sức khỏe tổng thể thế nào?",
            "Da liễu: SPF trong kem chống nắng nghĩa là gì?",
            "Collagen có thực sự giúp da không?",
            "Detox cơ thể có khoa học không?",
            "Nước ép (juice cleanse) có lợi không?",
            "Thực phẩm chức năng (supplement) có cần thiết không?",
            "Rau củ quả nên ăn bao nhiêu/ngày?",
            "Thịt đỏ tiêu thụ bao nhiêu là an toàn?",
            "Rượu bia ảnh hưởng đến gan thế nào?",
        ],
    },
    {
        "name": "Space & Astronomy",
        "user": "stress_user_07",
        "questions": [
            "Vũ trụ bắt đầu như thế nào? Big Bang là gì?",
            "Vũ trụ có bao nhiêu tuổi?",
            "Dải Ngân Hà (Milky Way) lớn thế nào?",
            "Hệ Mặt Trời gồm những hành tinh nào?",
            "Mặt Trời thuộc loại sao nào?",
            "Vòng đời của một ngôi sao diễn ra thế nào?",
            "Lỗ đen (Black Hole) là gì?",
            "Sao neutron là gì?",
            "Supernova là gì?",
            "Dark matter là gì? Tại sao khó phát hiện?",
            "Dark energy là gì?",
            "Vũ trụ có đang giãn nở không?",
            "Hằng số Hubble là gì?",
            "Ánh sáng mất bao lâu từ Mặt Trời đến Trái Đất?",
            "Một năm ánh sáng là bao xa?",
            "Sao gần Trái Đất nhất (ngoài Mặt Trời) là gì?",
            "Ngân hà Andromeda có va chạm với Milky Way không?",
            "Exoplanet là gì? Làm sao phát hiện?",
            "Zona sinh sống (Goldilocks zone) là gì?",
            "Có sự sống ngoài Trái Đất không?",
            "Mặt Trăng hình thành thế nào?",
            "Thủy triều do Mặt Trăng hay Mặt Trời gây ra?",
            "Sao Hỏa có nước không?",
            "Sao Kim sao lại nóng hơn Sao Thủy?",
            "Sao Mộc (Jupiter) đặc biệt thế nào?",
            "Sao Thổ (Saturn) có gì đặc trưng?",
            "Sao Thiên Vương (Uranus) nghiêng theo hướng nào?",
            "Sao Hải Vương (Neptune) có bão lớn không?",
            "Diêm Vương Tinh (Pluto) tại sao không còn là hành tinh?",
            "Tiểu hành tinh (asteroid) và sao chổi (comet) khác nhau thế nào?",
            "Vành đai Kuiper là gì?",
            "Đám mây Oort là gì?",
            "Tàu Voyager 1 hiện đang ở đâu?",
            "James Webb Space Telescope khác Hubble thế nào?",
            "SpaceX đã đạt thành tựu gì?",
            "Starship rocket to thế nào?",
            "Trạm vũ trụ ISS quay quanh Trái Đất ở độ cao bao nhiêu?",
            "Người đầu tiên lên Mặt Trăng là ai?",
            "Chương trình Artemis NASA nhằm mục tiêu gì?",
            "Elon Musk muốn đưa người lên Sao Hỏa năm nào?",
            "Radio telescope (kính thiên văn vô tuyến) dùng để làm gì?",
            "SETI là gì?",
            "Tín hiệu Wow! là gì?",
            "Fermi Paradox là gì?",
            "Vũ trụ song song (multiverse) có tồn tại không?",
            "Thuyết tương đối đặc biệt của Einstein nói gì?",
            "Thuyết tương đối rộng nói gì?",
            "Sóng hấp dẫn (gravitational waves) được phát hiện thế nào?",
            "LIGO là gì?",
            "Hố đen siêu lớn ở trung tâm thiên hà là gì?",
        ],
    },
    {
        "name": "Cooking & Food",
        "user": "stress_user_08",
        "questions": [
            "Phở Hà Nội và phở Sài Gòn khác nhau thế nào?",
            "Cách nấu nước dùng phở chuẩn vị Hà Nội?",
            "Bún bò Huế đặc trưng của vùng nào?",
            "Bánh mì Việt Nam có nguồn gốc thế nào?",
            "Cơm tấm là đặc sản của vùng nào?",
            "Bún chả nổi tiếng ở đâu?",
            "Cao lầu chỉ có ở đâu?",
            "Mì Quảng là đặc sản vùng nào?",
            "Chả cá Lã Vọng là món gì?",
            "Bún đậu mắm tôm xuất xứ từ đâu?",
            "Cách làm kim chi cơ bản?",
            "Sushi và sashimi khác nhau thế nào?",
            "Ramen Nhật có bao nhiêu loại chính?",
            "Pad Thai là món gì?",
            "Tom Yum là súp của nước nào?",
            "Cà ri (curry) Ấn Độ và Thái Lan khác nhau thế nào?",
            "Hummus làm từ nguyên liệu gì?",
            "Pizza Neapolitan và New York style khác nhau thế nào?",
            "Pasta có bao nhiêu loại hình dạng?",
            "Risotto nấu thế nào?",
            "Croissant bánh mì bơ Pháp có kỹ thuật gì đặc biệt?",
            "Crème brûlée là gì?",
            "Tiramisu có nguồn gốc từ đâu?",
            "Đường caramel làm thế nào?",
            "Meringue làm thế nào?",
            "Soufflé tại sao khó làm?",
            "Nhiệt độ Maillard reaction là gì?",
            "Caramelization khác Maillard reaction thế nào?",
            "Tại sao steak nên để nghỉ sau khi nướng?",
            "Cách làm steak perfect medium rare?",
            "Sous vide cooking là gì?",
            "Air fryer có thực sự khỏe hơn chiên thường không?",
            "Lên men (fermentation) trong thực phẩm là gì?",
            "Kefir và yogurt khác nhau thế nào?",
            "Kombucha là gì?",
            "Cách làm bánh mì sourdough cơ bản?",
            "Gluten-free diet có thực sự cần thiết không?",
            "Umami là vị gì?",
            "MSG có thực sự hại không?",
            "Cách knife skills cơ bản trong bếp?",
            "Mise en place là gì?",
            "Stock và broth khác nhau thế nào?",
            "Cách làm sốt béchamel?",
            "Emulsification là gì? Ví dụ trong nấu ăn?",
            "Tại sao muối pasta water?",
            "Al dente nghĩa là gì?",
            "Cách chọn dầu ăn phù hợp cho từng loại nấu?",
            "Smoke point của dầu là gì?",
            "Cách bảo quản thảo mộc (herbs) tươi?",
            "Gia vị (spices) nên bảo quản thế nào?",
        ],
    },
    {
        "name": "Psychology & Behavior",
        "user": "stress_user_09",
        "questions": [
            "Tâm lý học (Psychology) nghiên cứu gì?",
            "Sigmund Freud đóng góp gì cho tâm lý học?",
            "Unconscious mind (vô thức) là gì?",
            "Cognitive psychology là gì?",
            "Cognitive bias là gì? Cho ví dụ.",
            "Confirmation bias ảnh hưởng đến quyết định thế nào?",
            "Dunning-Kruger effect là gì?",
            "Impostor syndrome là gì?",
            "Growth mindset vs fixed mindset khác nhau thế nào?",
            "Self-efficacy là gì?",
            "Intrinsic motivation vs extrinsic motivation?",
            "Maslow hierarchy of needs là gì?",
            "Flow state (trạng thái flow) là gì?",
            "Habit loop (vòng lặp thói quen) hoạt động thế nào?",
            "Dopamine có vai trò gì trong hành vi?",
            "Serotonin thiếu dẫn đến vấn đề gì?",
            "Cortisol liên quan đến stress thế nào?",
            "Fight-or-flight response là gì?",
            "PTSD (Post-traumatic stress disorder) là gì?",
            "CBT (Cognitive Behavioral Therapy) là gì?",
            "Mindfulness meditation có lợi ích khoa học gì?",
            "Social proof (bằng chứng xã hội) là gì?",
            "Authority bias ảnh hưởng đến hành vi thế nào?",
            "Anchoring bias trong quyết định là gì?",
            "Loss aversion là gì?",
            "Sunk cost fallacy là gì?",
            "Decision fatigue là gì?",
            "Procrastination nguyên nhân tâm lý là gì?",
            "Perfectionism có hại không?",
            "Resilience (khả năng phục hồi) là gì?",
            "Empathy và sympathy khác nhau thế nào?",
            "Active listening là gì?",
            "Non-violent communication (NVC) là gì?",
            "Attachment theory là gì?",
            "Love languages là gì? Có mấy loại?",
            "Gaslighting là gì?",
            "Narcissistic personality là gì?",
            "Emotional intelligence (EQ) là gì?",
            "IQ và EQ ai quan trọng hơn?",
            "Introvert và extrovert khác nhau thế nào?",
            "MBTI personality types có khoa học không?",
            "Big Five personality traits là gì?",
            "Nature vs nurture trong phát triển tính cách?",
            "Childhood trauma ảnh hưởng đến người trưởng thành thế nào?",
            "Self-compassion là gì?",
            "Gratitude practice có tác dụng khoa học không?",
            "Journaling có lợi ích tâm lý gì?",
            "Social media ảnh hưởng đến mental health thế nào?",
            "FOMO (Fear of Missing Out) là gì?",
            "Digital detox có cần thiết không?",
        ],
    },
    {
        "name": "Environment & Climate",
        "user": "stress_user_10",
        "questions": [
            "Biến đổi khí hậu là gì?",
            "Hiệu ứng nhà kính hoạt động thế nào?",
            "CO2 từ đâu ra nhiều nhất?",
            "Methane (CH4) nguy hiểm hơn CO2 thế nào?",
            "Paris Agreement năm 2015 về gì?",
            "Net zero emissions nghĩa là gì?",
            "Carbon footprint của một người bình thường là bao nhiêu?",
            "Carbon offset là gì? Có hiệu quả không?",
            "Năng lượng mặt trời (solar) hoạt động thế nào?",
            "Năng lượng gió (wind) hoạt động thế nào?",
            "Năng lượng thủy điện có ảnh hưởng môi trường không?",
            "Năng lượng hạt nhân có an toàn không?",
            "Electric vehicle (EV) thực sự xanh hơn xe xăng không?",
            "Battery lithium-ion có vấn đề môi trường gì?",
            "Tái chế (recycling) thực sự hiệu quả thế nào?",
            "Ocean plastic pollution nghiêm trọng thế nào?",
            "Microplastics trong cơ thể người là gì?",
            "Deforestation ảnh hưởng đến khí hậu thế nào?",
            "Rừng Amazon quan trọng thế nào?",
            "Coral reef bleaching là gì?",
            "Mực nước biển dâng bao nhiêu mỗi năm?",
            "Các thành phố nào sẽ bị ngập nước do nước biển dâng?",
            "Drought (hạn hán) ngày càng tệ hơn không?",
            "Wildfire (cháy rừng) liên quan đến khí hậu thế nào?",
            "Biodiversity loss nghiêm trọng thế nào?",
            "Species extinction rate hiện tại nhanh thế nào?",
            "Ecosystem services là gì?",
            "Sustainable agriculture là gì?",
            "Meat consumption ảnh hưởng đến môi trường thế nào?",
            "Veganism có thực sự tốt cho môi trường không?",
            "Food waste là vấn đề môi trường thế nào?",
            "Fast fashion ảnh hưởng môi trường thế nào?",
            "Circular economy là gì?",
            "Greenwashing là gì?",
            "ESG (Environmental, Social, Governance) là gì?",
            "Carbon tax hoạt động thế nào?",
            "Cap and trade system là gì?",
            "IPCC là gì?",
            "1.5°C warming target tại sao quan trọng?",
            "Tipping points trong khí hậu là gì?",
            "Permafrost tan chảy gây hậu quả gì?",
            "El Niño và La Niña là gì?",
            "Monsoon (mùa mưa nhiệt đới) ảnh hưởng bởi khí hậu thế nào?",
            "Air quality index (AQI) đọc thế nào?",
            "Smog khác mist thế nào?",
            "Acid rain là gì?",
            "Ozone layer depletion hiện tại tình trạng thế nào?",
            "CFC là gì? Tại sao bị cấm?",
            "Geoengineering là gì? Có nên làm không?",
            "Climate refugee là gì?",
        ],
    },
]

# Final question (Q50) appended to each topic
FINAL_QUESTION = (
    "Đây là câu hỏi cuối cùng của chúng ta. Bạn có thể tóm tắt lại "
    "tất cả các CHỦ ĐỀ và NỘI DUNG chính mà tôi đã hỏi bạn trong "
    "toàn bộ cuộc trò chuyện này không? Liệt kê ít nhất 10 điểm chính. "
    "Đây là bài kiểm tra bộ nhớ (memory test) của hệ thống."
)


# ── Data structures ───────────────────────────────────────────────────────────
@dataclass
class QuestionResult:
    user: str
    topic: str
    q_num: int
    ok: bool
    status: int | None
    latency_s: float
    session_id: str | None
    content_preview: str
    content_full: str = ""
    error: str = ""


@dataclass
class UserReport:
    user: str
    topic: str
    session_id: str | None
    total_questions: int
    ok_count: int
    error_count: int
    avg_latency_s: float
    max_latency_s: float
    min_latency_s: float
    final_q_ok: bool
    final_q_content: str
    memory_keywords_found: list[str]
    memory_score: float  # 0.0 – 1.0
    sentinels_expected: list[str] = field(default_factory=list)
    sentinels_found: list[str] = field(default_factory=list)
    sentinel_recall_rate: float | None = None
    wrong_user_sentinel_leaks: list[str] = field(default_factory=list)
    latencies_s: list[float] = field(default_factory=list)


# ── HTTP helper ───────────────────────────────────────────────────────────────
def sentinel_code(user_name: str, q_num: int) -> str:
    user_num = user_name.rsplit("_", 1)[-1]
    return f"STRESS-U{user_num}-Q{q_num:02d}"


def sentinel_turns_for_run() -> tuple[int, ...]:
    return tuple(q_num for q_num in SENTINEL_TURNS if q_num < NUM_QUESTIONS)


def sentinel_codes_for_user(user_name: str) -> list[str]:
    return [sentinel_code(user_name, q_num) for q_num in sentinel_turns_for_run()]


def all_sentinel_codes() -> list[str]:
    selected_topics = TOPICS[: min(USER_COUNT, len(TOPICS))]
    return [code for topic in selected_topics for code in sentinel_codes_for_user(topic["user"])]


def apply_answer_style(question: str) -> str:
    if ANSWER_STYLE != "brief":
        return question
    return (
        f"{question}\n\n"
        "Yêu cầu trả lời ngắn để kiểm thử hiệu năng: tối đa "
        f"{BRIEF_ANSWER_MAX_CHARS} ký tự, 2-4 gạch đầu dòng, không mở rộng ngoài câu hỏi."
    )


def with_sentinel(user_name: str, q_num: int, question: str) -> str:
    styled_question = apply_answer_style(question)
    if not USE_SENTINELS or q_num not in SENTINEL_TURNS:
        return styled_question
    code = sentinel_code(user_name, q_num)
    return (
        f"{styled_question}\n\n"
        f"SENTINEL MEMORY FACT: mã kiểm thử riêng của tôi ở câu {q_num} là {code}. "
        "Hãy ghi nhớ chính xác mã này cho câu hỏi cuối."
    )


def final_question_for_user(user_name: str) -> str:
    if not USE_SENTINELS:
        return FINAL_QUESTION
    expected = ", ".join(sentinel_codes_for_user(user_name))
    return (
        FINAL_QUESTION
        + "\n\nNgoài phần tóm tắt, hãy nhắc lại chính xác toàn bộ mã SENTINEL MEMORY FACT "
        + f"của riêng tôi. Các mã cần kiểm tra là: {expected}. "
        + "Không dùng mã của user khác."
    )


def load_api_key() -> str:
    env_path = Path("/home/hung/ai-hub/.env")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("API_KEY not found in .env")


def _post_chat_sync(api_key: str, payload: dict, timeout: float) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        f"{BASE_URL}/v1/chat",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-API-KEY": api_key,
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, {"content": body[:500], "session_id": None}


async def chat_async(
    api_key: str,
    user_name: str,
    message: str,
    session_id: str | None = None,
    q_num: int = 0,
    topic: str = "",
) -> QuestionResult:
    payload = {
        "project_id": PROJECT_ID,
        "tenant_id": TENANT_ID,
        "user_name": user_name,
        "user_message": message,
        "model_mode": MODEL_MODE,
        "enable_search": False,
    }
    if PROVIDER:
        payload["provider"] = PROVIDER
    if ALLOW_EXTERNAL:
        payload["allow_external"] = True
    if session_id:
        payload["session_id"] = session_id

    start = time.perf_counter()
    try:
        status, body = await asyncio.to_thread(
            _post_chat_sync, api_key, payload, TIMEOUT_SECONDS
        )
        latency = round(time.perf_counter() - start, 3)
        ok = 200 <= status < 300
        content = body.get("content", "") or ""
        return QuestionResult(
            user=user_name,
            topic=topic,
            q_num=q_num,
            ok=ok,
            status=status,
            latency_s=latency,
            session_id=body.get("session_id", session_id),
            content_preview=content[:PREVIEW_CHARS].replace("\n", " "),
            content_full=content,
        )
    except (URLError, TimeoutError, Exception) as exc:
        latency = round(time.perf_counter() - start, 3)
        return QuestionResult(
            user=user_name,
            topic=topic,
            q_num=q_num,
            ok=False,
            status=None,
            latency_s=latency,
            session_id=session_id,
            content_preview="",
            error=repr(exc)[:300],
        )


# ── Single user: ask 49 questions sequentially then 1 final ──────────────────
async def run_user_session(api_key: str, topic_def: dict) -> tuple[list[QuestionResult], str]:
    """Run questions for one user sequentially to preserve session memory."""
    user = topic_def["user"]
    topic_name = topic_def["name"]
    topic_question_count = NUM_QUESTIONS - 1
    questions = topic_def["questions"][:topic_question_count]
    all_results: list[QuestionResult] = []
    session_id: str | None = None

    print(f"  [START] {user} | Topic: {topic_name}")

    for idx, question in enumerate(questions, start=1):
        result = await chat_async(
            api_key, user, with_sentinel(user, idx, question),
            session_id=session_id, q_num=idx, topic=topic_name
        )
        session_id = result.session_id or session_id
        all_results.append(result)

        status_icon = "✓" if result.ok else "✗"
        print(
            f"  {status_icon} {user} Q{idx:02d}/{NUM_QUESTIONS} "
            f"latency={result.latency_s}s session={session_id}"
        )
        if USER_DELAY_SECONDS:
            await asyncio.sleep(USER_DELAY_SECONDS)

    final_q_num = len(questions) + 1
    final_result = await chat_async(
        api_key, user, final_question_for_user(user),
        session_id=session_id, q_num=final_q_num, topic=topic_name
    )
    all_results.append(final_result)

    print(f"\n{'='*60}")
    print(f"  🏁 {user} | Q50 MEMORY CHECK | ok={final_result.ok}")
    print(f"  Response preview:\n  {final_result.content_preview[:400]}")
    print(f"{'='*60}\n")

    return all_results, topic_name


# ── Run selected users concurrently ───────────────────────────────────────────
async def run_all_users(api_key: str) -> list[tuple[list[QuestionResult], str]]:
    """Launch selected users with optional global concurrency limit."""
    selected_topics = TOPICS[: min(USER_COUNT, len(TOPICS))]
    if MAX_CONCURRENCY <= 0:
        return await asyncio.gather(*(run_user_session(api_key, topic) for topic in selected_topics))

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def run_limited(topic: dict) -> tuple[list[QuestionResult], str]:
        async with semaphore:
            return await run_user_session(api_key, topic)

    return await asyncio.gather(*(run_limited(topic) for topic in selected_topics))


# ── Compute per-user report ───────────────────────────────────────────────────
def compute_report(results: list[QuestionResult], topic: str) -> UserReport:
    if not results:
        return UserReport(
            user="unknown", topic=topic, session_id=None,
            total_questions=0, ok_count=0, error_count=0,
            avg_latency_s=0, max_latency_s=0, min_latency_s=0,
            final_q_ok=False, final_q_content="",
            memory_keywords_found=[], memory_score=0.0,
            sentinels_expected=[], sentinels_found=[], sentinel_recall_rate=None,
            wrong_user_sentinel_leaks=[],
            latencies_s=[],
        )

    final = results[-1]  # Q50
    latencies = [r.latency_s for r in results]

    # Extract keywords from topic questions to check if memory recalled them
    topic_def = next((t for t in TOPICS if t["name"] == topic), None)
    memory_keywords: list[str] = []
    if topic_def:
        # Pick a sample of distinctive keywords from questions 1-49
        sample_words = []
        for q in topic_def["questions"][:10]:  # first 10 questions as anchors
            words = [w.strip("?.,") for w in q.split() if len(w) > 5]
            sample_words.extend(words[:2])
        memory_keywords = list(dict.fromkeys(sample_words))[:15]  # deduplicate

    final_content = final.content_full or final.content_preview or ""
    final_content_lower = final_content.lower()
    found_keywords = [kw for kw in memory_keywords if kw.lower() in final_content_lower]
    memory_score = len(found_keywords) / max(len(memory_keywords), 1)
    expected_sentinels = sentinel_codes_for_user(results[0].user) if USE_SENTINELS else []
    found_sentinels = [code for code in expected_sentinels if code in final_content]
    wrong_sentinel_leaks = [
        code
        for code in all_sentinel_codes()
        if code not in expected_sentinels and code in final_content
    ] if USE_SENTINELS else []
    sentinel_recall = len(found_sentinels) / len(expected_sentinels) if expected_sentinels else None

    return UserReport(
        user=results[0].user,
        topic=topic,
        session_id=final.session_id,
        total_questions=len(results),
        ok_count=sum(1 for r in results if r.ok),
        error_count=sum(1 for r in results if not r.ok),
        avg_latency_s=round(mean(latencies), 3),
        max_latency_s=round(max(latencies), 3),
        min_latency_s=round(min(latencies), 3),
        final_q_ok=final.ok,
        final_q_content=final.content_full or final.content_preview,
        memory_keywords_found=found_keywords,
        memory_score=round(memory_score, 3),
        sentinels_expected=expected_sentinels,
        sentinels_found=found_sentinels,
        sentinel_recall_rate=round(sentinel_recall, 3) if sentinel_recall is not None else None,
        wrong_user_sentinel_leaks=wrong_sentinel_leaks,
        latencies_s=latencies,
    )


# ── Print final summary ───────────────────────────────────────────────────────
def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return ordered[index]


# ── Print final summary ───────────────────────────────────────────────────────
def print_final_summary(reports: list[UserReport], wall_time_s: float) -> None:
    print("\n" + "=" * 70)
    print("  MEMORY STRESS TEST — FINAL REPORT")
    print("=" * 70)
    print(f"  Users tested     : {len(reports)}")
    print(f"  Questions/user   : {NUM_QUESTIONS} ({NUM_QUESTIONS - 1} topic + 1 memory check)")
    print(f"  Total questions  : {sum(r.total_questions for r in reports)}")
    print(f"  Max concurrency  : {MAX_CONCURRENCY or 'all users'}")
    print(f"  User delay       : {USER_DELAY_SECONDS}s")
    print(f"  Wall clock time  : {wall_time_s:.1f}s")
    print()

    all_ok = sum(r.ok_count for r in reports)
    all_err = sum(r.error_count for r in reports)
    all_total = sum(r.total_questions for r in reports)
    all_latencies = [r.avg_latency_s for r in reports]
    request_latencies = [latency for report in reports for latency in report.latencies_s]

    print(f"  Total OK         : {all_ok}/{all_total}  ({100*all_ok//max(all_total,1)}%)")
    print(f"  Total Errors     : {all_err}")
    print(f"  Avg latency/user : {round(mean(all_latencies),3)}s")
    print(f"  Latency p50/p95  : {percentile(request_latencies, 0.50):.3f}s / {percentile(request_latencies, 0.95):.3f}s")
    print(f"  Latency p99/max  : {percentile(request_latencies, 0.99):.3f}s / {max(request_latencies) if request_latencies else 0:.3f}s")
    if USE_SENTINELS:
        sentinel_scores = [r.sentinel_recall_rate for r in reports if r.sentinel_recall_rate is not None]
        leaks = sum(len(r.wrong_user_sentinel_leaks) for r in reports)
        print(f"  Sentinel recall  : {mean(sentinel_scores):.1%}" if sentinel_scores else "  Sentinel recall  : n/a")
        print(f"  Sentinel leaks   : {leaks}")
    print()

    print("  ─── Per-User Results ─────────────────────────────────────────")
    print(f"  {'User':<20} {'Topic':<25} {'OK':>4} {'Err':>4} {'AvgLat':>8} {'Q50':>4} {'MemScore':>9}")
    print(f"  {'-'*20} {'-'*25} {'-'*4} {'-'*4} {'-'*8} {'-'*4} {'-'*9}")

    for r in reports:
        topic_short = r.topic[:24]
        q50 = "✓" if r.final_q_ok else "✗"
        print(
            f"  {r.user:<20} {topic_short:<25} {r.ok_count:>4} {r.error_count:>4} "
            f"{r.avg_latency_s:>8.3f}s {q50:>4} {r.memory_score:>9.3f}"
        )

    print()
    print("  ─── Memory Check (Q50) Responses ────────────────────────────")
    for r in reports:
        print(f"\n  [{r.user}] Topic: {r.topic}")
        print(f"  Keywords found  : {r.memory_keywords_found}")
        print(f"  Memory score    : {r.memory_score:.1%}")
        if USE_SENTINELS:
            print(f"  Sentinels found : {r.sentinels_found}/{r.sentinels_expected}")
            print(f"  Sentinel recall : {r.sentinel_recall_rate:.1%}" if r.sentinel_recall_rate is not None else "  Sentinel recall : n/a")
            print(f"  Sentinel leaks  : {r.wrong_user_sentinel_leaks}")
        print(f"  Q50 response    : {r.final_q_content[:300]}")

    print()
    print("  ─── Aggregate Memory Score ───────────────────────────────────")
    scores = [r.memory_score for r in reports]
    print(f"  Mean  : {mean(scores):.1%}")
    print(f"  Median: {median(scores):.1%}")
    perfect = sum(1 for s in scores if s > 0.5)
    print(f"  Users with >50% recall: {perfect}/{len(scores)}")
    print("=" * 70)

    # Save JSON report
    report_path = Path("/home/hung/ai-hub/reports") / REPORT_NAME
    report_path.parent.mkdir(exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "base_url": BASE_URL,
                "tenant_id": TENANT_ID,
                "project_id": PROJECT_ID,
                "model_mode": MODEL_MODE,
                "provider": PROVIDER or "local",
                "allow_external": ALLOW_EXTERNAL,
                "use_sentinels": USE_SENTINELS,
                "sentinel_turns": list(sentinel_turns_for_run()),
                "configured_users": USER_COUNT,
                "configured_questions_per_user": NUM_QUESTIONS,
                "max_concurrency": MAX_CONCURRENCY,
                "user_delay_seconds": USER_DELAY_SECONDS,
                "preview_chars": PREVIEW_CHARS,
                "answer_style": ANSWER_STYLE,
                "brief_answer_max_chars": BRIEF_ANSWER_MAX_CHARS,
                "wall_time_s": round(wall_time_s, 2),
                "total_questions": all_total,
                "total_ok": all_ok,
                "total_errors": all_err,
                "latency_p50_s": round(percentile(request_latencies, 0.50), 3),
                "latency_p95_s": round(percentile(request_latencies, 0.95), 3),
                "latency_p99_s": round(percentile(request_latencies, 0.99), 3),
                "latency_max_s": round(max(request_latencies), 3) if request_latencies else 0,
                "users": [
                    {
                        "user": r.user,
                        "topic": r.topic,
                        "total": r.total_questions,
                        "ok": r.ok_count,
                        "errors": r.error_count,
                        "avg_latency_s": r.avg_latency_s,
                        "max_latency_s": r.max_latency_s,
                        "latencies_s": r.latencies_s,
                        "final_q_ok": r.final_q_ok,
                        "memory_score": r.memory_score,
                        "keywords_found": r.memory_keywords_found,
                        "sentinels_expected": r.sentinels_expected,
                        "sentinels_found": r.sentinels_found,
                        "sentinel_recall_rate": r.sentinel_recall_rate,
                        "wrong_user_sentinel_leaks": r.wrong_user_sentinel_leaks,
                        "q50_response": r.final_q_content,
                    }
                    for r in reports
                ],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n  JSON report saved → {report_path}")


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    api_key = load_api_key()

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║             MEMORY STRESS TEST — Concurrent Users           ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"  BASE_URL   : {BASE_URL}")
    print(f"  PROJECT    : {PROJECT_ID}")
    print(f"  TENANT     : {TENANT_ID}")
    print(f"  MODEL_MODE : {MODEL_MODE}")
    print(f"  PROVIDER   : {PROVIDER or 'local'}")
    print(f"  SENTINELS  : {USE_SENTINELS}")
    selected_topics = TOPICS[: min(USER_COUNT, len(TOPICS))]
    print(f"  Users      : {len(selected_topics)}")
    print(f"  Qs/user    : {NUM_QUESTIONS} ({NUM_QUESTIONS - 1} topic + 1 memory check)")
    print(f"  Concurrency: {MAX_CONCURRENCY or 'all users'}")
    print(f"  Answer mode: {ANSWER_STYLE}")
    print()
    print("  Topics:")
    for t in selected_topics:
        print(f"    • {t['user']} → {t['name']}")
    print()
    print(f"  ⚡ Launching {len(selected_topics)} users...\n")

    wall_start = time.perf_counter()
    all_user_results = await run_all_users(api_key)
    wall_time = time.perf_counter() - wall_start

    # Build reports
    reports: list[UserReport] = []
    for results_list, topic_name in all_user_results:
        report = compute_report(results_list, topic_name)
        reports.append(report)

    print_final_summary(reports, wall_time)


if __name__ == "__main__":
    asyncio.run(main())
