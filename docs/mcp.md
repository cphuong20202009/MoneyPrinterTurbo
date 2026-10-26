# MCP server: tạo video TikTok Shop bằng AI

`app/mcp_server.py` cho phép Claude Code (hoặc client MCP bất kỳ) điều khiển MoneyPrinterTurbo bằng lời, không cần mở WebUI hay API server. Video render ngay trong tiến trình của MCP server.

## Cài đặt

```bash
uv sync --extra mcp
```

Repo đã có sẵn `.mcp.json`. Mở Claude Code tại thư mục repo và đồng ý bật server `moneyprinter`.

Các key trong `config.toml`:

| Mục đích | Key | Bắt buộc? |
|---|---|---|
| Clip stock bù khi thiếu hình sản phẩm | `pexels_api_keys` (hoặc `pixabay_api_keys`) | Chỉ khi dùng stock |
| `generate_script`, `generate_tiktok_caption`, tự tạo từ khoá stock | `llm_provider` + key tương ứng | Không, nếu Claude tự viết kịch bản và truyền `stock_terms` |
| Giọng Azure V2 / Gemini / ElevenLabs / MiniMax / Fish Audio | key của từng hãng | Không, giọng Edge miễn phí |

## Tools

| Tool | Việc làm |
|---|---|
| `list_presets`, `get_preset_guide` | Kiểu nội dung: `review`, `kien_thuc`, `so_sanh`, `top_list`, `unboxing` |
| `list_voices`, `preview_voice` | Giọng đọc tiếng Việt và giọng Multilingual, cùng file mp3 nghe thử |
| `add_product_media`, `list_product_media` | Ảnh/clip sản phẩm thật lưu ở `storage/local_videos/products/<sản-phẩm>/` |
| `generate_script` | Kịch bản và từ khoá stock bằng LLM của repo |
| `create_video` | Render video 9:16 chạy nền, trả `task_id` |
| `get_task`, `list_tasks` | Tiến độ và đường dẫn `final-*.mp4` |
| `generate_tiktok_caption` | Tiêu đề, caption, 5 hashtag |

Hình trong video: file trong thư mục sản phẩm xuất hiện trước, theo thứ tự tên file (`01-...`, `02-...`). Nếu tổng thời lượng ngắn hơn lời đọc thì tải clip stock để bù (`stock_source`: `pexels` | `pixabay` | `coverr` | `none`).

## Ví dụ câu lệnh cho Claude

- "Thêm ảnh trong ~/Downloads/brita vào sản phẩm *may loc nuoc brita*."
- "Viết kịch bản review máy lọc nước Brita nhập Mỹ theo preset review, giọng HoaiMy, rồi tạo video."
- "Làm 3 video kiến thức về cách chọn vitamin cho người lớn tuổi, mỗi video một giọng Multilingual khác nhau."
- "Kiểm tra task vừa tạo, xong thì viết caption TikTok."
