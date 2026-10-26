"""
Per-product image/clip folders for TikTok Shop videos.

Files live under ``storage/local_videos/products/<slug>/`` so the existing
local-material pipeline (which only reads inside ``storage/local_videos``)
accepts them without any extra path allowance.
"""

from __future__ import annotations

import os
import re
import shutil
import unicodedata

from app.models import const
from app.models.schema import MaterialInfo
from app.utils import file_security, utils

_SUPPORTED_EXTENSIONS = set(const.FILE_TYPE_VIDEOS) | set(const.FILE_TYPE_IMAGES)


def slugify(name: str) -> str:
    """Turn a product name (Vietnamese allowed) into a safe folder name."""
    value = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    if not value:
        raise ValueError(f"product name {name!r} does not produce a usable folder name")
    return value[:80]


def products_root() -> str:
    return utils.storage_dir(os.path.join("local_videos", "products"), create=True)


def product_dir(product_slug: str, create: bool = False) -> str:
    slug = slugify(product_slug)
    path = os.path.join(products_root(), slug)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _is_supported(filename: str) -> bool:
    return utils.parse_extension(filename) in _SUPPORTED_EXTENSIONS


def list_products() -> list[str]:
    root = products_root()
    return sorted(
        entry for entry in os.listdir(root) if os.path.isdir(os.path.join(root, entry))
    )


def list_product_media(product_slug: str) -> list[str]:
    """Absolute paths of usable media in a product folder, sorted by name.

    Name order lets users control the timeline (01-hero.jpg, 02-unbox.mp4, ...).
    """
    directory = product_dir(product_slug)
    if not os.path.isdir(directory):
        return []
    files = []
    for entry in sorted(os.listdir(directory)):
        if entry.startswith(".") or not _is_supported(entry):
            continue
        path = file_security.resolve_path_within_directory(directory, entry)
        files.append(path)
    return files


def add_product_media(product_slug: str, source_paths: list[str]) -> dict:
    """Copy local files into the product folder.

    Sources are files the user points the AI at on their own machine; only the
    destination is constrained, and only image/video extensions are accepted.
    """
    directory = product_dir(product_slug, create=True)
    added, skipped = [], []
    for source in source_paths:
        source = os.path.expanduser(source)
        name = os.path.basename(source)
        if not os.path.isfile(source):
            skipped.append({"path": source, "reason": "not a file"})
            continue
        if not _is_supported(name):
            skipped.append({"path": source, "reason": "unsupported file type"})
            continue
        target = file_security.resolve_path_within_directory(
            directory, name, require_file=False
        )
        shutil.copy2(source, target)
        added.append(target)
    return {"product_dir": directory, "added": added, "skipped": skipped}


def build_materials(product_slug: str) -> list[MaterialInfo]:
    return [
        MaterialInfo(provider="local", url=path, duration=0)
        for path in list_product_media(product_slug)
    ]
