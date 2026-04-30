# Ghi chú nhanh: 2 tool mới cho CrewAI trong AI Hub

## Tóm tắt
Trong hệ thống hiện tại, 2 tool mới là:

1. `WebSearchTool` (`app/agents/tools.py`)
2. `DBConnectorTool` (`app/agents/tools.py`)

> Lưu ý: đây là **CrewAI BaseTool** (không phải LangChain Tool chạy trực tiếp trong `Agent(tools=...)`).

## 1) WebSearchTool làm gì?
- Được gắn cho **Researcher agent** (`app/agents/researcher.py`).
- Dùng `DDGS` để tìm kiếm web realtime.
- Input: chuỗi query.
- Output: danh sách kết quả dạng `title + snippet`.

Mục tiêu: cung cấp dữ liệu mới nhất từ internet cho tác vụ nghiên cứu.

## 2) DBConnectorTool làm gì?
- Được gắn cho **Analyst agent** (`app/agents/analyst.py`).
- Truy vấn SQLite chat history (`ai_hub.db`).
- Chỉ cho phép câu lệnh `SELECT` (read-only).
- Giới hạn trả về tối đa 20 dòng mỗi lần chạy.

Mục tiêu: lấy ngữ cảnh lịch sử nội bộ để phân tích theo dữ liệu thật của hệ thống.

## Hai tool phối hợp trong flow nào?
Trong `CrewService` (`app/agents/crew_service.py`):

1. **Research task**: Researcher dùng `WebSearchTool` để lấy thông tin ngoài web.
2. **Analysis task**: Analyst dùng `DBConnectorTool` + context từ research task để tổng hợp insight.
3. Kết quả trả qua endpoint `POST /v1/crew/research` (`app/routes/crew.py`).

## Giá trị thực tế cho hệ thống
- `WebSearchTool` = nguồn dữ liệu bên ngoài, cập nhật realtime.
- `DBConnectorTool` = nguồn dữ liệu nội bộ (lịch sử chat).
- Kết hợp lại giúp câu trả lời vừa có thông tin mới, vừa bám context của hệ thống bạn.

## Điều kiện bật tính năng
- Endpoint crew chỉ hoạt động khi `ENABLE_CREW_AGENTS=true`.
- Nếu crew service không bật, API trả 503.
