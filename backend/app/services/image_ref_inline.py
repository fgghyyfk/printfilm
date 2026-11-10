"""参考图内嵌：把本站图片压缩成 data URI 直接随请求发给上游。

上游（BytePlus / TokenFree）拉不到 localhost、内网或未开 OSS 的 /static 地址；
原图 base64 动辄数 MB 又容易让上游卡住，所以统一缩到长边 ≤1536 的 JPEG。
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

INLINE_MAX_EDGE = 1536
INLINE_JPEG_QUALITY = 88


def encode_ref_image(path: Path, max_edge: int = INLINE_MAX_EDGE) -> str:
    """读取本地图片，纠正 EXIF 方向、透明区域垫白、缩到长边 ≤max_edge，返回 JPEG data URI。"""
    with Image.open(path) as raw:
        image = ImageOps.exif_transpose(raw)
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            canvas = Image.new("RGB", rgba.size, (255, 255, 255))
            canvas.paste(rgba, mask=rgba.split()[-1])
            image = canvas
        else:
            image = image.convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=INLINE_JPEG_QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


async def inline_local_refs(urls: list[str]) -> list[str]:
    """本站能解析到本地文件的参考图改为压缩 data URI；已是 data URI 或外部地址的原样保留。"""
    from app.services import storage

    prepared: list[str] = []
    for url in urls:
        if not url or url.startswith("data:"):
            prepared.append(url)
            continue
        local = storage.local_path_from_url(url)
        if local and local.is_file():
            try:
                prepared.append(await asyncio.to_thread(encode_ref_image, local))
                continue
            except Exception as exc:  # noqa: BLE001 — 坏图回退原地址，交给上游报错
                logger.warning("参考图内嵌失败，保留原地址 path=%s err=%s", local, exc)
        prepared.append(url)
    return prepared
