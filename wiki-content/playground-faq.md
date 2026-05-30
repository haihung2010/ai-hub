# FAQ - Câu hỏi thường gặp về AI Chatbot

## Chatbot này là gì?

Đây là AI chatbot chạy hoàn toàn trên server nội bộ, sử dụng mô hình ngôn ngữ lớn (LLM) được tối ưu cho tiếng Việt và tiếng Anh. Chatbot có thể trả câu hỏi, viết code, dịch thuật, phân tích và nhiều tác vụ khác.

## Sử dụng mô hình AI nào?

Chatbot sử dụng **Gemma 4 E4B** — mô hình AI tiên tiến của Google, được tinh chỉnh và chạy trên GPU NVIDIA RTX 5060 Ti. Mô hình được cập nhật thường xuyên để cải thiện chất lượng.

## Chatbot có nhớ cuộc trò chuyện trước không?

**Có.** Chatbot lưu lịch sử trò chuyện theo từng người dùng và project. Bạn có thể:
- Tiếp tục cuộc trò chuyện cũ khi quay lại
- Xóa lịch sử bằng lệnh `/clear`
- Bắt đầu phiên mới bất cứ lúc nào

## Dữ liệu của tôi có được bảo mật không?

**Có.** Dữ liệu được lưu trên server nội bộ, không chia sẻ với bên thứ ba. Các biện pháp bảo mật:
- Mã hóa API key
- Rate limiting chống spam
- Mỗi người dùng có session riêng biệt
- Có thể xóa lịch sử bất cứ lúc nào

## Chatbot có giới hạn gì?

- **Không có internet**: Chatbot không thể truy cập web (trừ khi bật tính năng tìm kiếm)
- **Kiến thức có hạn**: Thông tin cập nhật đến thời điểm huấn luyện mô hình
- **Không thể thực thi code**: Chatbot viết code nhưng không chạy code trực tiếp
- **Có thể sai**: Luôn kiểm tra thông tin quan trọng từ nguồn chính thức

## Làm sao để có kết quả tốt hơn?

1. **Hỏi cụ thể**: "Giải thích OOP với ví dụ Python" tốt hơn "OOP là gì?"
2. **Cung cấp ngữ cảnh**: "Tôi là newbie, giải thích đơn giản về..."
3. **Yêu cầu format**: "Liệt kê 5 điểm chính", "Dạng bảng so sánh"
4. **Hỏi lại**: Nếu chưa hiểu, hãy hỏi "Giải thích chi tiết hơn" hoặc "Cho ví dụ khác"

## Lệnh hữu ích

| Lệnh | Chức năng |
|------|-----------|
| `/clear` | Xóa lịch sử trò chuyện |
| `/search: [câu hỏi]` | Tìm kiếm thông tin trên web |
| ⚡ Stream toggle | Bật/tắt chế độ trả lời realtime |

## Có lỗi thì làm sao?

Nếu chatbot trả lời sai hoặc gặp lỗi:
1. Thử hỏi lại với cách diễn đạt khác
2. Gõ `/clear` để bắt đầu lại
3. Báo lỗi cho quản trị viên qua email hoặc Zalo

## Chatbot có miễn phí không?

**Có**, chatbot miễn phí cho người dùng được cấp quyền truy cập. Liên hệ quản trị viên để được cấp API key.
