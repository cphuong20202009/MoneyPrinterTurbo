import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.services import buffer_post
from app.utils import utils


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "data": {
                "createPost": {
                    "post": {
                        "id": "buffer-post-1",
                        "text": "Demo subject",
                        "dueAt": "2026-06-15T10:00:00.000Z",
                        "status": "scheduled",
                        "assets": [{"source": "https://example.com/video.mp4"}],
                    }
                }
            }
        }


class TestBufferPostService(unittest.TestCase):
    def setUp(self):
        self.original_app_config = dict(config.app)

    def tearDown(self):
        config.app.clear()
        config.app.update(self.original_app_config)

    def test_queue_tiktok_video_adds_video_to_buffer_queue(self):
        task_id = "buffer-task"
        task_dir = utils.task_dir(task_id)
        video_path = os.path.join(task_dir, "final-1.mp4")
        Path(video_path).write_bytes(b"fake-video")
        config.app.update(
            {
                "buffer_enabled": True,
                "buffer_api_key": "buffer-key",
                "buffer_tiktok_channel_id": "channel-tiktok",
                "buffer_auto_queue": True,
                "cloudinary_enabled": False,
                "endpoint": "https://videos.example.com",
            }
        )

        try:
            with patch.object(
                buffer_post.requests, "post", return_value=_FakeResponse()
            ) as post:
                result = buffer_post.queue_tiktok_video(
                    task_id=task_id,
                    video_path=video_path,
                    title="Demo subject",
                    video_script="Script body",
                )

            self.assertTrue(result["success"])
            self.assertEqual(result["post"]["id"], "buffer-post-1")
            post.assert_called_once()
            request_kwargs = post.call_args.kwargs
            self.assertEqual(
                request_kwargs["headers"]["Authorization"], "Bearer buffer-key"
            )
            self.assertIn("mode: addToQueue", request_kwargs["json"]["query"])
            self.assertIn("ChannelId!", request_kwargs["json"]["query"])
            self.assertIn("video:", request_kwargs["json"]["query"])
            self.assertEqual(
                request_kwargs["json"]["variables"]["videoUrl"],
                f"https://videos.example.com/tasks/{task_id}/final-1.mp4",
            )
            self.assertEqual(
                request_kwargs["json"]["variables"]["channelId"], "channel-tiktok"
            )
            self.assertEqual(
                request_kwargs["json"]["variables"]["text"],
                "Demo subject\n\n#trendtiktok #cokiengcolanh #phongthuy",
            )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_build_post_text_appends_configured_hashtags_without_version_suffix(self):
        config.app["buffer_post_text"] = ""
        config.app["buffer_post_hashtags"] = "#trendtiktok #cokiengcolanh #phongthuy"

        text = buffer_post.buffer_post_service._build_post_text(
            title="Chủ Đề Video",
            video_script="Script body",
            version_index=2,
        )

        self.assertEqual(
            text,
            "Chủ Đề Video\n\n#trendtiktok #cokiengcolanh #phongthuy",
        )

    def test_queue_tiktok_video_uses_cloudinary_url_when_enabled(self):
        task_id = "buffer-cloudinary-task"
        task_dir = utils.task_dir(task_id)
        video_path = os.path.join(task_dir, "final-1.mp4")
        Path(video_path).write_bytes(b"fake-video")
        config.app.update(
            {
                "buffer_enabled": True,
                "buffer_api_key": "buffer-key",
                "buffer_tiktok_channel_id": "channel-tiktok",
                "buffer_auto_queue": True,
                "endpoint": "",
            }
        )

        try:
            with patch.object(
                buffer_post.cloudinary_upload.cloudinary_upload_service,
                "is_configured",
                return_value=True,
            ), patch.object(
                buffer_post.cloudinary_upload,
                "upload_task_video",
                return_value={
                    "success": True,
                    "secure_url": "https://res.cloudinary.com/demo/video/upload/final.mp4",
                },
            ), patch.object(
                buffer_post.requests, "post", return_value=_FakeResponse()
            ) as post:
                result = buffer_post.queue_tiktok_video(
                    task_id=task_id,
                    video_path=video_path,
                    title="Demo subject",
                    video_script="Script body",
                )

            self.assertTrue(result["success"])
            self.assertEqual(
                post.call_args.kwargs["json"]["variables"]["videoUrl"],
                "https://res.cloudinary.com/demo/video/upload/final.mp4",
            )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
