import json
import os
import uuid
from unittest.mock import patch

import pytest

pytest.importorskip("mcp")

from app import mcp_server
from app.models.schema import VideoParams
from app.services import product_media, tiktok_shop
from app.services import task as tm
from app.services.state import MemoryState


@pytest.fixture
def storage(tmp_path):
    with patch.object(product_media.utils, "storage_dir") as storage_dir:
        def fake_storage_dir(sub_dir="", create=False):
            path = os.path.join(tmp_path, sub_dir)
            if create:
                os.makedirs(path, exist_ok=True)
            return path

        storage_dir.side_effect = fake_storage_dir
        yield tmp_path


@pytest.mark.parametrize("preset", sorted(tiktok_shop.PRESETS))
def test_every_preset_builds_valid_vietnamese_params(preset):
    params = tiktok_shop.build_video_params(preset, "Máy lọc nước", {"voice_rate": 1.2})
    assert params.video_subject == "Máy lọc nước"
    assert params.video_aspect.value == "9:16"
    assert params.video_language == "vi"
    assert params.voice_rate == 1.2
    assert params.custom_system_prompt == tiktok_shop.PRESETS[preset]["system_prompt"]


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError, match="unknown preset"):
        tiktok_shop.get_preset("dance")


def test_slugify_handles_vietnamese_and_traversal():
    assert product_media.slugify("Máy lọc nước Brita Đức") == "may-loc-nuoc-brita-duc"
    assert product_media.slugify("../../etc") == "etc"
    with pytest.raises(ValueError):
        product_media.slugify("../")


def test_add_product_media_copies_only_supported_files(storage, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "02-box.jpg").write_bytes(b"x")
    (src / "01-hero.mp4").write_bytes(b"x")
    (src / "notes.txt").write_text("x")

    result = product_media.add_product_media(
        "Brita", [str(src / "02-box.jpg"), str(src / "01-hero.mp4"), str(src / "notes.txt")]
    )

    assert len(result["added"]) == 2
    assert result["skipped"][0]["reason"] == "unsupported file type"
    files = [os.path.basename(p) for p in product_media.list_product_media("Brita")]
    assert files == ["01-hero.mp4", "02-box.jpg"]


def test_create_video_prefers_product_media_with_stock_fill(storage, tmp_path):
    src = tmp_path / "hero.jpg"
    src.write_bytes(b"x")
    product_media.add_product_media("Brita", [str(src)])
    state = MemoryState()
    with (patch.object(mcp_server.sm, "state", state),
          patch.object(mcp_server._executor, "submit") as submit):
        result = mcp_server.create_video(
            product="Brita", script="Kịch bản", stock_terms=["water filter"]
        )

    params = submit.call_args.args[2]
    assert result["video_source"] == "local"
    assert params.stock_fill_source == "pexels"
    assert params.video_terms == ["water filter"]
    assert params.video_materials[0].url.endswith("hero.jpg")
    assert state.get_task(result["task_id"]) is not None


def test_create_video_without_media_uses_stock_source(storage):
    with (patch.object(mcp_server.sm, "state", MemoryState()),
          patch.object(mcp_server._executor, "submit") as submit):
        result = mcp_server.create_video(product="Nothing here", stock_source="pixabay")
    assert result["video_source"] == "pixabay"
    assert submit.call_args.args[2].stock_fill_source is None


def test_create_video_without_any_source_fails(storage):
    with pytest.raises(ValueError, match="no media found"):
        mcp_server.create_video(product="Nothing here", stock_source="none")


def test_get_task_rejects_non_uuid_and_reads_completed_disk_task(tmp_path):
    with pytest.raises(ValueError, match="invalid task id"):
        mcp_server.get_task("../secret")

    task_id = str(uuid.uuid4())
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "final-1.mp4").write_bytes(b"x")
    with (patch.object(mcp_server.sm, "state", MemoryState()),
          patch.object(mcp_server.utils, "storage_dir", return_value=str(tmp_path / "tasks"))):
        task = mcp_server.get_task(task_id)
    assert task["state"] == "complete"
    assert task["videos"][0].endswith("final-1.mp4")


def _local_params(**kwargs):
    return VideoParams(video_subject="x", video_source="local", video_clip_duration=3, **kwargs)


def test_stock_fill_downloads_only_uncovered_duration():
    params = _local_params(stock_fill_source="pexels")
    with (patch.object(tm.material, "_get_downloaded_video_duration", return_value=4.0),
          patch.object(tm.material, "download_videos", return_value=["stock.mp4"]) as download):
        result = tm._download_stock_fill("t", params, ["water"], 20.0, ["a.mp4", "b.mp4"])
    assert result == ["stock.mp4"]
    assert download.call_args.kwargs["audio_duration"] == pytest.approx(12.0)
    assert download.call_args.kwargs["source"] == "pexels"


def test_stock_fill_skipped_when_local_covers_audio():
    params = _local_params(stock_fill_source="pexels")
    with (patch.object(tm.material, "_get_downloaded_video_duration", return_value=30.0),
          patch.object(tm.material, "download_videos") as download):
        assert tm._download_stock_fill("t", params, ["water"], 20.0, ["a.mp4"]) == []
    download.assert_not_called()


def test_stock_fill_failure_keeps_local_materials():
    params = _local_params(stock_fill_source="pexels")
    with (patch.object(tm.material, "_get_downloaded_video_duration", return_value=1.0),
          patch.object(tm.material, "download_videos", side_effect=RuntimeError("down"))):
        assert tm._download_stock_fill("t", params, ["water"], 20.0, ["a.mp4"]) == []


def test_local_materials_without_stock_fill_do_not_download():
    params = _local_params(video_materials=[{"provider": "local", "url": "a.mp4"}])
    with (patch.object(tm.video, "preprocess_video", return_value=params.video_materials),
          patch.object(tm.material, "download_videos") as download):
        assert tm.get_video_materials("t", params, "", 20.0) == ["a.mp4"]
    download.assert_not_called()


def test_list_voices_marks_free_vietnamese_voices_ready():
    result = mcp_server.list_voices()
    edge = result["groups"][0]
    assert edge["ready"] is True
    assert "vi-VN-HoaiMyNeural-Female" in edge["voices"]
    assert json.dumps(result)
