"""商品带货：把套图底图（1:1 / 3:4）补成视频画幅（如 9:16），背景用同图高斯模糊填充，商品不被裁切。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

RATIO_SIZE = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
    "3:4": (1080, 1440),
    "21:9": (2520, 1080),
}


# 视频画幅对应的像素尺寸，未知回落 9:16
def frame_size(ratio: str) -> tuple[int, int]:
    return RATIO_SIZE.get((ratio or "").strip(), RATIO_SIZE["9:16"])


def pad_to_frame(src: Path, dest: Path, *, ratio: str) -> Path:
    """原图等比放进目标画幅居中，四周用放大模糊的同图填充，保存为 JPEG。"""
    W, H = frame_size(ratio)
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGB")
    bg = ImageOps.fit(img, (W, H), method=Image.Resampling.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=max(W, H) // 40))
    # 压暗一点，突出主体
    bg = Image.eval(bg, lambda v: int(v * 0.82))
    fg = img.copy()
    fg.thumbnail((W, H), Image.Resampling.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    dest.parent.mkdir(parents=True, exist_ok=True)
    bg.save(dest, format="JPEG", quality=92)
    return dest
