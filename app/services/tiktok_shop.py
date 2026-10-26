"""
TikTok Shop content presets for a Vietnamese channel selling US-imported goods.

Each preset bundles a Vietnamese script system prompt with render defaults tuned
for vertical short video (9:16, word-by-word subtitles, product footage first).
The MCP server exposes these so an AI client can produce videos without the
WebUI; the API, CLI and WebUI are unaffected.
"""

from __future__ import annotations

from typing import Any

from app.models.schema import VideoParams

DEFAULT_VOICE_NAME = "vi-VN-HoaiMyNeural-Female"
DEFAULT_FONT_NAME = "BeVietnamPro-Bold.ttf"

# Shared rules appended to every preset. They keep scripts short enough for
# readable subtitles and avoid claims that TikTok Shop moderation rejects.
_COMMON_RULES = """
## Quy tắc chung:
1. Viết bằng tiếng Việt tự nhiên, giọng nói chuyện như người thật, xưng "mình" và gọi người xem là "bạn" hoặc "mọi người".
2. Câu đầu tiên là HOOK gây tò mò hoặc chạm nỗi đau, đọc dưới 3 giây. Không chào hỏi, không giới thiệu kênh.
3. Câu ngắn (tối đa khoảng 15 từ), mỗi câu một ý, dễ đọc trên phụ đề.
4. Chỉ nói "hàng nhập Mỹ", "chính hãng" khi thông tin sản phẩm có ghi rõ điều đó. Không bịa nguồn gốc, giá, chứng nhận.
5. Không khẳng định chữa bệnh, cam kết kết quả tuyệt đối, so sánh hạ thấp thương hiệu khác hay dùng từ ngữ tuyệt đối ("số 1", "tốt nhất thế giới").
6. Kết thúc bằng một lời kêu gọi ngắn: bấm giỏ hàng / follow để xem thêm.
7. Chỉ trả về lời thoại thuần, không markdown, không tiêu đề, không ghi chú cảnh quay, không ghi "người dẫn".
""".strip()


def _prompt(role: str, goal: str, structure: str) -> str:
    return f"""
# Role: {role}

## Mục tiêu:
{goal}

## Cấu trúc:
{structure}

{_COMMON_RULES}
""".strip()


PRESETS: dict[str, dict[str, Any]] = {
    "review": {
        "label": "Review sản phẩm",
        "description": "Review trung thực một sản phẩm: vấn đề, trải nghiệm, ưu/nhược, ai nên mua.",
        "paragraph_number": 1,
        "system_prompt": _prompt(
            "TikTok Shop product reviewer",
            "Viết kịch bản review 30-45 giây giúp người xem tin tưởng và muốn bấm giỏ hàng.",
            "Hook -> vấn đề người dùng gặp -> trải nghiệm thật với sản phẩm (2-3 điểm cụ thể) "
            "-> một nhược điểm nhỏ để tăng độ tin cậy -> ai nên mua -> CTA.",
        ),
    },
    "kien_thuc": {
        "label": "Chia sẻ kiến thức",
        "description": "Mẹo / kiến thức hữu ích liên quan sản phẩm để hút follow, sản phẩm chỉ xuất hiện khéo léo.",
        "paragraph_number": 1,
        "system_prompt": _prompt(
            "Educational short-video creator",
            "Viết kịch bản 30-50 giây chia sẻ kiến thức thực sự hữu ích, khiến người xem muốn lưu video và follow.",
            "Hook bằng một sự thật bất ngờ hoặc sai lầm phổ biến -> giải thích ngắn gọn -> 2-3 mẹo áp dụng ngay "
            "-> nhắc nhẹ sản phẩm như một cách làm dễ hơn -> CTA follow để xem phần tiếp.",
        ),
    },
    "so_sanh": {
        "label": "So sánh / phân biệt",
        "description": "So sánh hai lựa chọn hoặc cách phân biệt hàng thật - hàng giả.",
        "paragraph_number": 1,
        "system_prompt": _prompt(
            "Product comparison creator",
            "Viết kịch bản 30-45 giây so sánh khách quan giúp người xem chọn đúng.",
            "Hook đặt câu hỏi chọn cái nào -> 3 tiêu chí so sánh rõ ràng -> kết luận phù hợp với ai -> CTA.",
        ),
    },
    "top_list": {
        "label": "Top N món nên mua",
        "description": "Danh sách top vài món hàng Mỹ đáng mua trong một chủ đề.",
        "paragraph_number": 1,
        "system_prompt": _prompt(
            "Listicle short-video creator",
            "Viết kịch bản 40-55 giây dạng danh sách, giữ chân người xem tới món cuối.",
            "Hook hứa hẹn món cuối hay nhất -> từng món: tên + một lý do đáng mua (1-2 câu) "
            "-> món cuối nổi bật nhất -> CTA bấm giỏ hàng.",
        ),
    },
    "unboxing": {
        "label": "Unboxing / đập hộp",
        "description": "Đập hộp hàng mới về, nhấn cảm giác hồi hộp và chi tiết sản phẩm.",
        "paragraph_number": 1,
        "system_prompt": _prompt(
            "Unboxing short-video creator",
            "Viết kịch bản 25-40 giây đập hộp tạo cảm giác mới lạ, chân thật.",
            "Hook về món hàng vừa về -> mở hộp, mô tả bao bì và phụ kiện -> chi tiết ấn tượng nhất "
            "-> cảm nhận đầu tiên -> CTA.",
        ),
    },
}

DEFAULT_PRESET = "review"


def get_preset(name: str | None) -> dict[str, Any]:
    key = (name or DEFAULT_PRESET).strip().lower()
    if key not in PRESETS:
        raise ValueError(
            f"unknown preset {name!r}; available: {', '.join(sorted(PRESETS))}"
        )
    return PRESETS[key]


def list_presets() -> list[dict[str, str]]:
    return [
        {"name": name, "label": preset["label"], "description": preset["description"]}
        for name, preset in PRESETS.items()
    ]


def build_script_requirements(product_info: str = "", notes: str = "") -> str:
    """Runtime context passed to the LLM as ``video_script_prompt``."""
    parts = []
    if product_info.strip():
        parts.append(f"Thông tin sản phẩm (chỉ dùng thông tin này, không bịa thêm):\n{product_info.strip()}")
    if notes.strip():
        parts.append(f"Yêu cầu thêm:\n{notes.strip()}")
    return "\n\n".join(parts)


def default_params() -> dict[str, Any]:
    return {
        "video_aspect": "9:16",
        "video_language": "vi",
        "voice_name": DEFAULT_VOICE_NAME,
        "voice_rate": 1.1,
        "font_name": DEFAULT_FONT_NAME,
        "font_size": 64,
        "stroke_width": 2.0,
        "subtitle_enabled": True,
        "subtitle_position": "two_thirds_bottom",
        "subtitle_display_mode": "word_by_word",
        "subtitle_animation": "pop_spring",
        "bgm_type": "random",
        "bgm_volume": 0.15,
        "video_concat_mode": "sequential",
        "video_clip_duration": 3,
    }


def build_video_params(
    preset: str | None,
    video_subject: str,
    overrides: dict[str, Any] | None = None,
) -> VideoParams:
    """Merge preset defaults with caller overrides and validate them."""
    selected = get_preset(preset)
    values = default_params()
    values["paragraph_number"] = selected["paragraph_number"]
    values["custom_system_prompt"] = selected["system_prompt"]
    values.update({k: v for k, v in (overrides or {}).items() if v is not None})
    values["video_subject"] = video_subject
    return VideoParams(**values)
