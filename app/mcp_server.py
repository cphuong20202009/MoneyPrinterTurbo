"""
MCP server: drive MoneyPrinterTurbo from an AI client (e.g. Claude Code).

Runs the video pipeline in-process, so neither the API server nor the WebUI
needs to be running. Rendering is slow, so ``create_video`` returns a task id
immediately and the client polls ``get_task``.

Run:  uv run --extra mcp python -m app.mcp_server
"""

from __future__ import annotations

import glob
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger
from mcp.server.mcpserver import MCPServer

from app.config import config
from app.models import const
from app.models.schema import VideoParams
from app.services import llm, product_media, tiktok_shop, voice
from app.services import state as sm
from app.services import task as tm
from app.utils import utils

INSTRUCTIONS = """
Tạo video ngắn TikTok Shop (tiếng Việt) bằng MoneyPrinterTurbo.

Quy trình gợi ý:
1. list_presets / list_voices để chọn kiểu nội dung và giọng đọc.
2. (tuỳ chọn) add_product_media để đưa ảnh/clip sản phẩm thật vào thư mục sản phẩm.
3. Tự viết kịch bản tiếng Việt theo hướng dẫn của preset (get_preset_guide), hoặc gọi generate_script.
4. create_video với script + stock_terms (từ khoá TIẾNG ANH để tìm clip stock) -> nhận task_id.
5. get_task(task_id) tới khi state = complete, rồi generate_tiktok_caption để lấy caption + hashtag.
"""

mcp = MCPServer(name="moneyprinter", instructions=INSTRUCTIONS)

# Rendering is CPU/FFmpeg heavy; one worker queues jobs instead of thrashing.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-video")
_task_subjects: dict[str, str] = {}
_task_subjects_lock = threading.Lock()

_STATE_NAMES = {
    const.TASK_STATE_PROCESSING: "processing",
    const.TASK_STATE_COMPLETE: "complete",
    const.TASK_STATE_FAILED: "failed",
}

_EDGE_VIETNAMESE_VOICES = [
    "vi-VN-HoaiMyNeural-Female",
    "vi-VN-NamMinhNeural-Male",
]


def _multilingual_azure_voices(v2: bool = False) -> list[str]:
    # The bundled catalog lists both the free Edge name and its "-V2" Azure
    # Speech variant; keep the two groups apart so "ready" stays accurate.
    return [
        v for v in voice.get_all_azure_voices()
        if "Multilingual" in v and ("-V2-" in v) == v2
    ]


@mcp.tool()
def list_presets() -> list[dict[str, str]]:
    """Liệt kê các kiểu nội dung TikTok Shop (review, kiến thức, so sánh, top list, unboxing)."""
    return tiktok_shop.list_presets()


@mcp.tool()
def get_preset_guide(preset: str = tiktok_shop.DEFAULT_PRESET) -> dict[str, Any]:
    """Trả về hướng dẫn viết kịch bản (system prompt) của một preset để AI tự viết kịch bản đúng phong cách."""
    selected = tiktok_shop.get_preset(preset)
    return {
        "preset": preset,
        "label": selected["label"],
        "script_guide": selected["system_prompt"],
        "render_defaults": tiktok_shop.default_params(),
    }


@mcp.tool()
def list_voices() -> dict[str, Any]:
    """Liệt kê giọng đọc dùng được cho tiếng Việt, nhóm theo nhà cung cấp và cho biết nhóm nào sẵn sàng.

    Giọng Multilingual (Ava, Andrew, Emma, Brian...) đọc được tiếng Việt với chất giọng khác HoaiMy/NamMinh.
    """
    groups: list[dict[str, Any]] = [
        {
            "provider": "edge (miễn phí)",
            "ready": True,
            "voices": _EDGE_VIETNAMESE_VOICES,
        },
        {
            "provider": "edge multilingual (miễn phí, đọc được tiếng Việt)",
            "ready": True,
            "voices": _multilingual_azure_voices(),
        },
    ]

    azure_ready = bool(config.azure.get("speech_key"))
    groups.append({
        "provider": "azure v2 (cần [azure] speech_key)",
        "ready": azure_ready,
        "voices": [
            v.replace("-Female", "-V2-Female").replace("-Male", "-V2-Male")
            for v in _EDGE_VIETNAMESE_VOICES
        ] + _multilingual_azure_voices(v2=True),
    })
    groups.append({
        "provider": "gemini (cần gemini_api_key, đa ngôn ngữ)",
        "ready": bool(config.app.get("gemini_api_key")),
        "voices": voice.get_gemini_voices(),
    })
    elevenlabs_key = voice.get_elevenlabs_api_key()
    groups.append({
        "provider": "elevenlabs (cần API key, đa ngôn ngữ, giọng yêu thích trong tài khoản)",
        "ready": bool(elevenlabs_key),
        "voices": voice.get_elevenlabs_voices(elevenlabs_key) if elevenlabs_key else [],
    })
    for label, get_key, get_voices in (
        ("minimax (cần API key)", voice.get_minimax_tts_api_key, voice.get_minimax_voices),
        ("fish_audio (cần API key, hỗ trợ giọng clone)", voice.get_fish_audio_api_key, voice.get_fish_audio_voices),
    ):
        try:
            ready = bool(get_key())
            voices = get_voices()
        except Exception as exc:
            logger.warning(f"voice listing failed for {label}: {exc}")
            ready, voices = False, []
        groups.append({"provider": label, "ready": ready, "voices": voices})

    return {"default": tiktok_shop.DEFAULT_VOICE_NAME, "groups": groups}


@mcp.tool()
def preview_voice(
    voice_name: str,
    text: str = "Chào mọi người, hôm nay mình review một món hàng Mỹ cực đáng tiền.",
    voice_rate: float = 1.1,
) -> dict[str, Any]:
    """Tạo file mp3 nghe thử một giọng đọc, trả về đường dẫn file."""
    out_dir = utils.storage_dir("voice_previews", create=True)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in voice_name)
    voice_file = os.path.join(out_dir, f"{safe_name}.mp3")
    result = voice.tts(
        text=text, voice_name=voice_name, voice_rate=voice_rate, voice_file=voice_file
    )
    if result is None or not os.path.isfile(voice_file):
        raise RuntimeError(f"TTS failed for voice {voice_name!r}; check the provider key in config.toml")
    return {"voice_name": voice_name, "file": voice_file}


@mcp.tool()
def generate_script(
    product: str,
    preset: str = tiktok_shop.DEFAULT_PRESET,
    product_info: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Dùng LLM cấu hình trong config.toml để viết kịch bản tiếng Việt + từ khoá stock (tiếng Anh).

    Nếu chưa cấu hình LLM, hãy tự viết kịch bản theo get_preset_guide rồi truyền vào create_video.
    """
    selected = tiktok_shop.get_preset(preset)
    script = llm.generate_script(
        video_subject=product,
        language="vi",
        paragraph_number=selected["paragraph_number"],
        video_script_prompt=tiktok_shop.build_script_requirements(product_info, notes),
        custom_system_prompt=selected["system_prompt"],
    )
    if not script:
        raise RuntimeError("LLM returned no script; check llm_provider and its API key in config.toml")
    terms = llm.generate_terms(video_subject=product, video_script=script, amount=5)
    return {"script": script, "stock_terms": terms}


@mcp.tool()
def add_product_media(product: str, paths: list[str]) -> dict[str, Any]:
    """Copy ảnh/clip sản phẩm thật (jpg, png, mp4, mov...) vào thư mục sản phẩm.

    Tên file quyết định thứ tự xuất hiện trong video (đặt 01-, 02-...).
    """
    return product_media.add_product_media(product, paths)


@mcp.tool()
def list_product_media(product: str = "") -> dict[str, Any]:
    """Không truyền product: liệt kê các sản phẩm đã có. Truyền product: liệt kê file của sản phẩm đó."""
    if not product:
        return {"root": product_media.products_root(), "products": product_media.list_products()}
    return {
        "product_dir": product_media.product_dir(product),
        "files": product_media.list_product_media(product),
    }


def _run_task(task_id: str, params: VideoParams) -> None:
    try:
        tm.start(task_id=task_id, params=params, stop_at="video")
    except Exception as exc:
        # tm.start already converts pipeline errors; this only guards the worker.
        logger.exception(f"mcp video task crashed: {task_id}")
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED, error=str(exc))


@mcp.tool()
def create_video(
    product: str,
    preset: str = tiktok_shop.DEFAULT_PRESET,
    script: str = "",
    stock_terms: list[str] | None = None,
    voice_name: str = tiktok_shop.DEFAULT_VOICE_NAME,
    use_product_media: bool = True,
    stock_source: str = "pexels",
    video_count: int = 1,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tạo video 9:16 chạy nền, trả về task_id ngay (render mất vài phút; theo dõi bằng get_task).

    - script: kịch bản tiếng Việt. Bỏ trống thì LLM trong config.toml tự viết theo preset.
    - stock_terms: từ khoá TIẾNG ANH để tìm clip stock (vd ["water filter", "kitchen sink"]).
      Bỏ trống thì LLM tự tạo.
    - use_product_media: dùng ảnh/clip trong thư mục sản phẩm trước, thiếu thời lượng thì bù stock.
    - stock_source: pexels | pixabay | coverr | none (none = chỉ dùng hình sản phẩm).
    - overrides: ghi đè tham số VideoParams bất kỳ (vd {"voice_rate": 1.2, "bgm_volume": 0.1}).
    """
    values: dict[str, Any] = dict(overrides or {})
    values.update({
        "voice_name": voice_name,
        "video_script": script,
        "video_count": video_count,
    })
    if stock_terms:
        values["video_terms"] = stock_terms
    if not script:
        values["video_script_prompt"] = tiktok_shop.build_script_requirements(product)

    stock = None if stock_source == "none" else stock_source
    materials = product_media.build_materials(product) if use_product_media else []
    if materials:
        values["video_source"] = "local"
        values["video_materials"] = materials
        values["stock_fill_source"] = stock
    elif stock:
        values["video_source"] = stock
    else:
        raise ValueError(
            f"no media found for product {product!r} and stock_source is 'none'; "
            "add files with add_product_media or choose a stock source"
        )

    params = tiktok_shop.build_video_params(preset, product, values)
    task_id = utils.get_uuid()
    sm.state.update_task(task_id)
    with _task_subjects_lock:
        _task_subjects[task_id] = product
    _executor.submit(_run_task, task_id, params)
    return {
        "task_id": task_id,
        "video_source": params.video_source,
        "product_files": len(materials),
        "stock_fill": params.stock_fill_source if materials else None,
    }


def _completed_task_from_disk(task_id: str) -> dict[str, Any] | None:
    """Recover results of tasks rendered before this server process started."""
    directory = os.path.join(utils.storage_dir("tasks"), task_id)
    videos = sorted(glob.glob(os.path.join(directory, "final-*.mp4")))
    if not videos:
        return None
    return {"task_id": task_id, "state": "complete", "progress": 100, "videos": videos}


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    result = {
        "task_id": task.get("task_id"),
        "subject": _task_subjects.get(task.get("task_id", "")),
        "state": _STATE_NAMES.get(task.get("state"), task.get("state")),
        "progress": task.get("progress"),
    }
    for key in ("videos", "script", "terms", "warnings", "failed_stage", "error"):
        if task.get(key):
            result[key] = task[key]
    return result


@mcp.tool()
def get_task(task_id: str) -> dict[str, Any]:
    """Xem trạng thái task: processing / complete / failed, % tiến độ, đường dẫn file mp4 khi xong."""
    try:
        # Task ids are also directory names; reject anything else before touching disk.
        task_id = str(uuid.UUID(task_id))
    except ValueError:
        raise ValueError(f"invalid task id: {task_id!r}") from None
    task = sm.state.get_task(task_id)
    if task is None:
        task = _completed_task_from_disk(task_id)
        if task is None:
            raise ValueError(f"task {task_id} not found")
        return task
    return _public_task(task)


@mcp.tool()
def list_tasks(page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """Liệt kê các task đã tạo trong phiên server hiện tại (hoặc trong Redis nếu đã bật)."""
    tasks, total = sm.state.get_all_tasks(page, page_size)
    return {"total": total, "tasks": [_public_task(t) for t in tasks]}


@mcp.tool()
def generate_tiktok_caption(subject: str, script: str = "") -> dict[str, Any]:
    """Tạo tiêu đề, caption (kèm CTA) và 5 hashtag tiếng Việt cho TikTok bằng LLM trong config.toml."""
    return llm.generate_social_metadata(
        video_subject=subject, video_script=script, language="vi", platform="tiktok"
    )


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
