"""
Cloudinary upload integration for hosting generated videos.

Cloudinary video uploads use the `/video/upload` REST endpoint and return a
stable `secure_url` that can be passed to Buffer.
"""
import hashlib
import os
import time

import requests
from loguru import logger

from app.config import config
from app.utils import file_security, utils


class CloudinaryUploadService:
    def _get_config(self) -> dict:
        return {
            "enabled": bool(config.app.get("cloudinary_enabled", False)),
            "cloud_name": (config.app.get("cloudinary_cloud_name", "") or "").strip(),
            "api_key": (config.app.get("cloudinary_api_key", "") or "").strip(),
            "api_secret": (config.app.get("cloudinary_api_secret", "") or "").strip(),
            "folder": (config.app.get("cloudinary_folder", "") or "").strip().strip("/"),
        }

    def is_configured(self) -> bool:
        cfg = self._get_config()
        return bool(
            cfg["enabled"]
            and cfg["cloud_name"]
            and cfg["api_key"]
            and cfg["api_secret"]
        )

    @staticmethod
    def _sign_params(params: dict, api_secret: str) -> str:
        signable_items = []
        for key, value in sorted(params.items()):
            if value is None or value == "":
                continue
            if key in {"file", "api_key", "resource_type", "cloud_name"}:
                continue
            signable_items.append(f"{key}={value}")

        payload = "&".join(signable_items) + api_secret
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def upload_task_video(self, task_id: str, video_path: str) -> dict:
        if not self.is_configured():
            logger.warning("Cloudinary is not configured. Skipping video upload.")
            return {"success": False, "error": "Cloudinary is not configured"}

        try:
            resolved_path = file_security.resolve_path_within_directory(
                utils.task_dir(), video_path
            )
        except ValueError as exc:
            logger.warning(f"Cannot upload video outside task directory: {str(exc)}")
            return {"success": False, "error": str(exc)}

        relative_path = os.path.relpath(resolved_path, utils.task_dir()).replace(
            "\\", "/"
        )
        if not relative_path.startswith(f"{task_id}/"):
            return {
                "success": False,
                "error": "video path does not belong to the task directory",
            }

        cfg = self._get_config()
        stem = os.path.splitext(os.path.basename(resolved_path))[0]
        public_id = f"{task_id}/{stem}"
        timestamp = int(time.time())
        params = {
            "timestamp": timestamp,
            "public_id": public_id,
        }
        if cfg["folder"]:
            params["folder"] = cfg["folder"]

        signature = self._sign_params(params, cfg["api_secret"])
        upload_url = (
            f"https://api.cloudinary.com/v1_1/{cfg['cloud_name']}/video/upload"
        )
        data = {
            **params,
            "api_key": cfg["api_key"],
            "signature": signature,
        }

        logger.info(f"Uploading video to Cloudinary: {resolved_path}")
        try:
            with open(resolved_path, "rb") as video_file:
                response = requests.post(
                    upload_url,
                    data=data,
                    files={"file": video_file},
                    timeout=600,
                )
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.RequestException as exc:
            logger.error(f"Failed to upload video to Cloudinary: {str(exc)}")
            return {"success": False, "error": str(exc)}

        secure_url = payload.get("secure_url")
        if not secure_url:
            logger.warning(f"Cloudinary upload response missing secure_url: {payload}")
            return {
                "success": False,
                "error": "Cloudinary upload response missing secure_url",
                "response": payload,
            }

        logger.info(f"Uploaded video to Cloudinary: {secure_url}")
        return {
            "success": True,
            "secure_url": secure_url,
            "public_id": payload.get("public_id"),
            "response": payload,
        }


cloudinary_upload_service = CloudinaryUploadService()


def upload_task_video(task_id: str, video_path: str) -> dict:
    return cloudinary_upload_service.upload_task_video(task_id, video_path)
