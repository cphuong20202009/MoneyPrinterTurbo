"""
Buffer API integration for queueing generated videos to TikTok.

Buffer's public API accepts media by public URL, not by direct file upload.
Set `endpoint` in config.toml to a public base URL that can serve task videos.
"""
import os
from typing import Optional

import requests
from loguru import logger

from app.config import config
from app.services import cloudinary_upload
from app.utils import file_security, utils


class BufferPostService:
    API_BASE = "https://api.buffer.com"

    def _get_config(self) -> dict:
        return {
            "enabled": bool(config.app.get("buffer_enabled", False)),
            "api_key": (config.app.get("buffer_api_key", "") or "").strip(),
            "tiktok_channel_id": (
                config.app.get("buffer_tiktok_channel_id", "") or ""
            ).strip(),
            "auto_queue": bool(config.app.get("buffer_auto_queue", False)),
            "post_text": (config.app.get("buffer_post_text", "") or "").strip(),
            "post_hashtags": (
                config.app.get(
                    "buffer_post_hashtags",
                    "#trendtiktok #cokiengcolanh #phongthuy",
                )
                or ""
            ).strip(),
            "endpoint": (config.app.get("endpoint", "") or "").strip().rstrip("/"),
        }

    @property
    def auto_queue(self) -> bool:
        return self._get_config()["auto_queue"]

    def is_configured(self) -> bool:
        cfg = self._get_config()
        return bool(
            cfg["enabled"]
            and cfg["api_key"]
            and cfg["tiktok_channel_id"]
            and (cfg["endpoint"] or cloudinary_upload.cloudinary_upload_service.is_configured())
        )

    def _build_video_url(self, task_id: str, video_path: str) -> str:
        if cloudinary_upload.cloudinary_upload_service.is_configured():
            result = cloudinary_upload.upload_task_video(task_id, video_path)
            if result.get("success") and result.get("secure_url"):
                return result["secure_url"]
            raise ValueError(
                f"Cloudinary upload failed: {result.get('error', 'Unknown error')}"
            )

        cfg = self._get_config()
        if not cfg["endpoint"]:
            raise ValueError(
                "config.app.endpoint or Cloudinary config is required for Buffer video URLs"
            )

        resolved_path = file_security.resolve_path_within_directory(
            utils.task_dir(), video_path
        )
        relative_path = os.path.relpath(resolved_path, utils.task_dir()).replace(
            "\\", "/"
        )
        if not relative_path.startswith(f"{task_id}/"):
            raise ValueError("video path does not belong to the task directory")

        return f"{cfg['endpoint']}/tasks/{relative_path}"

    def _build_post_text(
        self, title: str, video_script: str = "", version_index: Optional[int] = None
    ) -> str:
        cfg = self._get_config()
        if cfg["post_text"]:
            return cfg["post_text"][:2200]

        text = (title or "").strip()
        if not text and video_script:
            text = video_script.strip().splitlines()[0]
        if not text:
            text = "New video"

        if cfg["post_hashtags"]:
            text = f"{text}\n\n{cfg['post_hashtags']}"

        return text[:2200]

    def queue_tiktok_video(
        self,
        task_id: str,
        video_path: str,
        title: str = "",
        video_script: str = "",
        version_index: Optional[int] = None,
    ) -> dict:
        if not self.is_configured():
            logger.warning("Buffer is not configured. Skipping TikTok queue post.")
            return {"success": False, "error": "Buffer is not configured"}

        cfg = self._get_config()

        try:
            video_url = self._build_video_url(task_id, video_path)
        except ValueError as exc:
            logger.warning(f"Cannot build Buffer video URL: {str(exc)}")
            return {"success": False, "error": str(exc)}

        post_text = self._build_post_text(title, video_script, version_index)
        query = """
        mutation CreateVideoPost($text: String!, $channelId: ChannelId!, $videoUrl: String!) {
          createPost(
            input: {
              text: $text
              channelId: $channelId
              schedulingType: automatic
              mode: addToQueue
              assets: [
                {
                  video: {
                    url: $videoUrl
                  }
                }
              ]
            }
          ) {
            ... on PostActionSuccess {
              post {
                id
                text
                dueAt
                status
                assets {
                  source
                }
              }
            }
            ... on MutationError {
              message
            }
          }
        }
        """

        logger.info(f"Queueing TikTok video to Buffer: {video_url}")
        try:
            response = requests.post(
                self.API_BASE,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cfg['api_key']}",
                },
                json={
                    "query": query,
                    "variables": {
                        "text": post_text,
                        "channelId": cfg["tiktok_channel_id"],
                        "videoUrl": video_url,
                    },
                },
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.RequestException as exc:
            logger.error(f"Failed to queue TikTok video to Buffer: {str(exc)}")
            return {
                "success": False,
                "error": str(exc),
                "video_url": video_url,
            }

        if payload.get("errors"):
            logger.warning(f"Buffer GraphQL errors: {payload['errors']}")
            return {
                "success": False,
                "error": payload["errors"],
                "video_url": video_url,
                "response": payload,
            }

        result = (payload.get("data") or {}).get("createPost") or {}
        if result.get("message"):
            logger.warning(f"Buffer rejected TikTok video: {result['message']}")
            return {
                "success": False,
                "error": result["message"],
                "video_url": video_url,
                "response": payload,
            }

        post = result.get("post")
        if not post:
            logger.warning(f"Unexpected Buffer response: {payload}")
            return {
                "success": False,
                "error": "Unexpected Buffer response",
                "video_url": video_url,
                "response": payload,
            }

        logger.info(f"Queued TikTok video to Buffer post: {post.get('id')}")
        return {
            "success": True,
            "post": post,
            "video_url": video_url,
            "response": payload,
        }


buffer_post_service = BufferPostService()


def queue_tiktok_video(
    task_id: str,
    video_path: str,
    title: str = "",
    video_script: str = "",
    version_index: Optional[int] = None,
) -> dict:
    return buffer_post_service.queue_tiktok_video(
        task_id=task_id,
        video_path=video_path,
        title=title,
        video_script=video_script,
        version_index=version_index,
    )
