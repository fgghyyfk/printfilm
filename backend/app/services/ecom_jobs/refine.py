"""精修提示词与纯 Pillow 四边泛洪去白；白色主体内部图案保持不透明。"""
from __future__ import annotations

from collections import deque

from PIL import Image, ImageChops

from app.services.compliance.ad_words import check_text

NEGATIVE_PROMPT = "商品变形，logo变形，新增部件，新增文字，水印，模糊，失真"


# 构建精修提示词，透明底先要求纯白背景供本地泛洪处理。
def build_prompt(params: dict) -> str:
    background = params.get("background", "white")
    note, _ = check_text(str(params.get("notes") or ""))
    scene = "保留原图背景和构图，只改善画质与光照" if background == "keep" else (
        "商品完整居中，背景纯白，无阴影、无渐变，与主体边缘清晰分离"
    )
    return "。".join(part for part in [
        "图1为产品主体，精修商品摄影，保持外形、比例、颜色、logo和包装文字完全一致，不增删部件",
        scene, "清理污点与噪点，保留真实材质，不添加文字、水印或广告语", note,
    ] if part)


# 从四边连通的近白区域移除 alpha；容差 0–80，不影响封闭白色主体细节。
def remove_white_background(image: Image.Image, tolerance: int = 20) -> Image.Image:
    rgba = image.convert("RGBA")
    tolerance = max(0, min(80, tolerance))
    r, g, b, _ = rgba.split()
    white = ImageChops.darker(ImageChops.darker(r, g), b).point(
        lambda value: 1 if value >= 255 - tolerance else 0
    )
    width, height = rgba.size
    mask = bytearray(white.tobytes())
    alpha = bytearray(rgba.getchannel("A").tobytes())
    # 扫描线队列避免为 4K 图每个像素保留 Python 元组。
    queue = deque((x, y) for y in (0, height - 1) for x in range(width))
    queue.extend((x, y) for x in (0, width - 1) for y in range(height))
    while queue:
        x, y = queue.popleft()
        if not mask[y * width + x]:
            continue
        left = x
        while left > 0 and mask[y * width + left - 1]:
            left -= 1
        right = x
        while right + 1 < width and mask[y * width + right + 1]:
            right += 1
        start, end = y * width + left, y * width + right + 1
        mask[start:end] = b"\0" * (end - start)
        alpha[start:end] = b"\0" * (end - start)
        for adjacent_y in (y - 1, y + 1):
            if 0 <= adjacent_y < height:
                in_run = False
                for adjacent_x in range(left, right + 1):
                    eligible = bool(mask[adjacent_y * width + adjacent_x])
                    if eligible and not in_run:
                        queue.append((adjacent_x, adjacent_y))
                    in_run = eligible
    rgba.putalpha(Image.frombytes("L", rgba.size, bytes(alpha)))
    return rgba
