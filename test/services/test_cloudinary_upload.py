import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.services import cloudinary_upload
from app.utils import utils


class _FakeCloudinaryResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "secure_url": "https://res.cloudinary.com/demo/video/upload/v1/final.mp4",
            "public_id": "moneyprinterturbo/task-id/final-1",
        }


class TestCloudinaryUploadService(unittest.TestCase):
    def setUp(self):
        self.original_app_config = dict(config.app)

    def tearDown(self):
        config.app.clear()
        config.app.update(self.original_app_config)

    def test_upload_task_video_returns_secure_url(self):
        task_id = "cloudinary-task"
        task_dir = utils.task_dir(task_id)
        video_path = os.path.join(task_dir, "final-1.mp4")
        Path(video_path).write_bytes(b"fake-video")
        config.app.update(
            {
                "cloudinary_enabled": True,
                "cloudinary_cloud_name": "demo",
                "cloudinary_api_key": "api-key",
                "cloudinary_api_secret": "api-secret",
                "cloudinary_folder": "moneyprinterturbo",
            }
        )

        try:
            with patch.object(
                cloudinary_upload.requests,
                "post",
                return_value=_FakeCloudinaryResponse(),
            ) as post:
                result = cloudinary_upload.upload_task_video(task_id, video_path)

            self.assertTrue(result["success"])
            self.assertEqual(
                result["secure_url"],
                "https://res.cloudinary.com/demo/video/upload/v1/final.mp4",
            )
            post.assert_called_once()
            self.assertEqual(
                post.call_args.args[0],
                "https://api.cloudinary.com/v1_1/demo/video/upload",
            )
            self.assertEqual(post.call_args.kwargs["data"]["api_key"], "api-key")
            self.assertEqual(
                post.call_args.kwargs["data"]["folder"], "moneyprinterturbo"
            )
            self.assertEqual(
                post.call_args.kwargs["data"]["public_id"],
                f"{task_id}/final-1",
            )
            self.assertIn("signature", post.call_args.kwargs["data"])
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
