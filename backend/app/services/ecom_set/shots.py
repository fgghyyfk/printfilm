"""电商图镜头库（构图模板）：每种镜头 = 给 AI 的机位/构图描述 + 摆位参数（位置、大小、平放、倾斜）。

标品合成时摆位图按镜头变化，AI 围绕摆好的商品搭景，原图再贴回——构图是真的在变，不只是改一句描述。
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from app.services.ecom_set.composite import Placement


@dataclass(frozen=True)
class Shot:
    key: str
    name: str
    prompt: str
    cx: float = 0.5        # 商品底边中心 x（0~1）
    base: float = 0.86     # 商品底边 y（0~1）
    scale: float = 1.0     # 相对场景默认商品高度的倍数
    angle: float = 0.0     # 逆时针倾斜角度（度）
    lay: bool = False      # 平放（商品转 90° 躺在台面上）


SHOTS: dict[str, Shot] = {s.key: s for s in (
    Shot("eye_center", "平视居中", "正机位平视拍摄，商品居中，左右对称的稳定构图"),
    Shot("eye_left", "平视偏左", "平视拍摄，商品位于画面左侧三分线，右侧留出空间摆放元素", cx=0.36),
    Shot("eye_right", "平视偏右", "平视拍摄，商品位于画面右侧三分线，左侧留出空间摆放元素", cx=0.64),
    Shot("low_hero", "低角度仰拍", "低机位略仰拍，商品显得挺拔有力量感，背景简洁", scale=1.12, base=0.9),
    Shot("high_45", "45°俯拍", "约 45 度俯拍，能看到台面与元素的层次，商品略小、环境更丰富", scale=0.82, base=0.8),
    Shot("close_up", "近景特写", "近距离拍摄，商品占画面大部分，浅景深，元素只在边缘虚化出现", scale=1.3, base=0.95),
    Shot("tilt_lean", "倾斜斜靠", "商品略微倾斜，自然斜靠在旁边的元素上，构图灵动", angle=10.0, cx=0.46),
    Shot("flat_lay", "正俯平铺", "正上方俯拍，商品平放在台面上，元素围绕商品平铺排布，几何感构图",
         lay=True, scale=0.9, base=0.72),
    Shot("reflection", "水面倒影", "商品立在平静的浅水面上，下方有清晰柔和的倒影", base=0.72, scale=0.9),
    Shot("ingredient_pile", "原料点缀", "商品旁点缀一两样新鲜原料，原料小而精、比例真实，画面留白充足，不堆满",
         scale=0.9, base=0.84),
)}

# 不再分配的镜头：45° 俯拍时贴回的商品仍是正面视角，透视对不上，画面不和谐（老套图里的保留可读）
RETIRED_SHOTS = {"high_45"}

# 各槽位的镜头偏好（第一个是默认）；规划模型可在其中选，整套尽量不重复
SLOT_SHOTS: dict[str, tuple[str, ...]] = {
    "goods_cover": ("eye_center", "low_hero", "reflection"),
    "goods_scene": ("eye_left", "eye_right", "eye_center"),
    "goods_selling": ("eye_right", "close_up", "tilt_lean", "eye_left"),
    "goods_selling2": ("tilt_lean", "eye_left", "reflection"),
    "goods_pain": ("eye_left", "eye_right", "close_up"),
    "goods_usage": ("eye_left", "eye_right", "close_up"),
    "goods_ingredient_still": ("ingredient_pile", "eye_center"),
    "goods_flatlay": ("flat_lay",),
    # 工具不用仰拍：仰拍时 AI 倾向于换角度重画整机（常见镜像），原图贴不回去
    "tool_cover": ("eye_center", "eye_left"),
    "tool_power": ("eye_right", "eye_left"),
    "tool_scene": ("eye_left", "eye_right", "eye_center"),
    "tool_workbench": ("eye_center", "eye_left", "eye_right"),
    "tool_pain": ("eye_left", "eye_right"),
}
ANY_SHOTS = tuple(k for k in SHOTS if k not in {"flat_lay", "ingredient_pile"} | RETIRED_SHOTS)


def shot_choices(slot_key: str) -> tuple[str, ...]:
    """某槽位可用的镜头（没有专门配置的合成槽位可用全部常规镜头）。"""
    return SLOT_SHOTS.get(slot_key, ANY_SHOTS)


def get(key: str | None) -> Shot | None:
    return SHOTS.get(key or "")


def options() -> list[dict]:
    return [{"key": s.key, "name": s.name} for s in SHOTS.values() if s.key not in RETIRED_SHOTS]


def transform(cut: Image.Image, shot: Shot | None) -> Image.Image:
    """按镜头处理商品抠图：平放转 90°、倾斜按角度旋转（扩边保留透明）。"""
    if shot is None:
        return cut
    out = cut
    if shot.lay:
        out = out.rotate(-90, expand=True, resample=Image.Resampling.BICUBIC)  # 顺时针：竖排标签转成从左往右读
    if shot.angle:
        out = out.rotate(shot.angle, expand=True, resample=Image.Resampling.BICUBIC)
        box = out.getchannel("A").getbbox()
        out = out.crop(box) if box else out
    return out


def placement(shot: Shot | None, *, height: float, blank: str, frame_ratio: float, aspect: float) -> Placement:
    """镜头 → 单件商品摆位：大小随镜头缩放，并保证不超出画面、不压到文字留白。"""
    base = 0.86 if blank != "bottom" else 0.68
    if shot is None:
        return Placement(0.5, base, height)
    h = height * shot.scale
    b = shot.base if blank != "bottom" else min(shot.base, 0.68)
    # 留白在上方时商品顶部不高于画面 32%；宽度不超出画面 84%
    h = min(h, b - (0.32 if blank == "top" else 0.08))
    w = h * aspect / frame_ratio
    if w > 0.84:
        h = h * 0.84 / w
        w = 0.84
    cx = min(max(shot.cx, w / 2 + 0.06), 1 - w / 2 - 0.06)
    return Placement(cx, b, max(0.15, h))
