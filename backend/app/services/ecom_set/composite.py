"""标品场景图合成：先按比例摆产品 → AI 围绕产品搭景 → 找到 AI 画的产品位置 → 品牌原抠图精确贴回 + 光色迁移。

产品像素始终来自原图（文字、logo 不会被 AI 改写），场景、道具、阴影来自 AI，比例由摆位决定。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

# 合成用生图尺寸（≤236 万像素，上游按 1.5K 计价；详情页 790~1440 宽足够）
COMPOSITE_SIZE = {"1:1": (1536, 1536), "3:4": (1328, 1776)}
COARSE_STEP = 4


@dataclass(frozen=True)
class Placement:
    """一支产品在画面里的摆位（相对画面的比例）。"""

    cx: float      # 底边中心 x（0~1）
    base: float    # 底边 y（0~1）
    height: float  # 产品高度占画面高度


# ---------- 抠图 ----------
def has_alpha(path: Path) -> bool:
    """图片是否带有效透明通道（真正抠过图的 PNG）。"""
    with Image.open(path) as im:
        if im.mode not in ("RGBA", "LA", "P"):
            return False
        a = im.convert("RGBA").getchannel("A")
        lo, _ = a.getextrema()
        return lo < 250


def load_cutout(path: Path) -> Image.Image:
    """读取抠图并裁到不透明区域。"""
    with Image.open(path) as src:
        im = ImageOps.exif_transpose(src).convert("RGBA")
    box = im.getchannel("A").getbbox()
    return im.crop(box) if box else im


def flatten_white(path: Path) -> Image.Image:
    """透明图铺白底转 RGB（给视觉模型、排版等不认 alpha 的环节用）。"""
    with Image.open(path) as src:
        im = ImageOps.exif_transpose(src)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            return bg
        return im.convert("RGB")


# ---------- ① 摆位 ----------
def lineup_placements(n: int, *, height: float, base: float = 0.86, margin: float = 0.08,
                      aspect: float = 0.32, frame_ratio: float = 0.75, gap: float = 0.45) -> list[Placement]:
    """n 支产品居中成组排开，互不接触。

    aspect：产品宽/高；frame_ratio：画面宽/高；gap：相邻产品间距占产品宽度的比例。
    产品高度会自动缩小，保证 n 支 + 间距放得进画面宽度（留 margin 边距）。
    """
    if n <= 1:
        return [Placement(0.5, base, height)]
    # 每支宽度（占画面宽）= height * aspect / frame_ratio；总宽 = n*w + (n-1)*gap*w ≤ 1 - 2*margin
    max_w = (1 - 2 * margin) / (n + (n - 1) * gap)
    w = min(height * aspect / frame_ratio, max_w)
    h = w * frame_ratio / aspect
    step = w * (1 + gap)
    start = 0.5 - step * (n - 1) / 2
    return [Placement(start + i * step, base, h) for i in range(n)]


def place_products(cutouts: list[Image.Image], placements: list[Placement], size: tuple[int, int],
                   bg_color: tuple[int, int, int]) -> tuple[Image.Image, list[tuple[int, int, int, int]]]:
    """在纯色底上按摆位放产品，返回 (参考图, 每支产品的 (x, y, w, h))。"""
    W, H = size
    canvas = Image.new("RGBA", size, (*bg_color, 255))
    boxes = []
    for cut, pl in zip(cutouts, placements, strict=True):
        h = int(H * pl.height)
        w = max(1, int(cut.width * h / cut.height))
        x, y = int(W * pl.cx - w / 2), int(H * pl.base - h)
        canvas.alpha_composite(cut.resize((w, h), Image.Resampling.LANCZOS), (x, y))
        boxes.append((x, y, w, h))
    return canvas.convert("RGB"), boxes


# ---------- ③ 定位：带遮罩的多尺度边缘匹配 ----------
def _edges(im: Image.Image) -> np.ndarray:
    """边缘强度图：白底白管靠轮廓与文字边缘区分，比亮度可靠。"""
    g = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    e = np.sqrt(gx ** 2 + gy ** 2)
    return e / (e.max() + 1e-6)


def _corr(a: np.ndarray, k: np.ndarray) -> np.ndarray:
    """valid 区域互相关（FFT）。"""
    H, W = a.shape
    h, w = k.shape
    full = np.fft.irfft2(np.fft.rfft2(a, s=(H, W)) * np.conj(np.fft.rfft2(k, s=(H, W))), s=(H, W))
    return full[: H - h + 1, : W - w + 1]


def _ssd_map(img_e: np.ndarray, tpl_e: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """逐位置遮罩内均方差（越小越像）。"""
    s1 = _corr(img_e ** 2, mask)
    s2 = _corr(img_e, mask * tpl_e)
    s3 = float((mask * tpl_e ** 2).sum())
    return (s1 - 2 * s2 + s3) / max(float(mask.sum()), 1.0)


def _template(cut: Image.Image, h: int) -> tuple[np.ndarray, np.ndarray, int, int]:
    w = max(1, int(cut.width * h / cut.height))
    t = cut.resize((w, h), Image.Resampling.BILINEAR)
    mask = (np.asarray(t.getchannel("A"), dtype=np.float32) > 20).astype(np.float32)
    return _edges(t), mask, w, h


def _best(img: Image.Image, cut: Image.Image, heights: list[int], step: int) -> tuple[float, int, int, int, int]:
    """在 img 里按若干高度搜 cut，返回 (分数, x, y, w, h)（原尺寸坐标）。"""
    small = img.resize((max(1, img.width // step), max(1, img.height // step)), Image.Resampling.BILINEAR)
    e = _edges(small)
    best = (np.inf, 0, 0, 0, 0)
    for h in heights:
        hs = max(8, h // step)
        if hs >= e.shape[0]:
            continue
        te, m, ws, _ = _template(cut, hs)
        if ws >= e.shape[1]:
            continue
        ssd = _ssd_map(e, te, m)
        y, x = np.unravel_index(np.argmin(ssd), ssd.shape)
        if ssd[y, x] < best[0]:
            best = (float(ssd[y, x]), int(x * step), int(y * step), int(ws * step), int(hs * step))
    return best


def locate(ai: Image.Image, cutouts: list[Image.Image], boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """找每支产品在 AI 图里的实际位置。

    单支：在预期位置附近搜。多支：AI 常会重新排间距，先用第一支的形状在整条横带上找出 n 个不重叠的位置，
    按从左到右顺序对应到产品，再逐支在附近精搜。
    """
    W, H = ai.size
    n = len(cutouts)
    exp_h = int(np.median([b[3] for b in boxes]))
    heights = [int(exp_h * (0.8 + 0.03 * i)) for i in range(17)]
    if n > 1:
        return [_refine(ai, cut, box) for cut, box in zip(cutouts, _lineup_anchors(ai, cutouts[0], heights, n, boxes), strict=True)]
    x, y, w, h = boxes[0]
    anchors = [(max(0, x - w), max(0, y - int(h * 0.5)), min(W, x + 2 * w), min(H, y + int(h * 1.35)))]
    out = []
    for cut, win in zip(cutouts, anchors, strict=True):
        sub = ai.crop(win)
        _, cx, cy, cw, ch = _best(sub, cut, heights, COARSE_STEP)
        pad = max(8, ch // 8)
        fx0, fy0 = max(0, win[0] + cx - pad), max(0, win[1] + cy - pad)
        fine_win = (fx0, fy0, min(W, win[0] + cx + cw + pad), min(H, win[1] + cy + ch + pad))
        fine_heights = [int(ch * (0.95 + 0.01 * i)) for i in range(11)]
        _, x, y, w, h = _best(ai.crop(fine_win), cut, fine_heights, 1)
        out.append((fine_win[0] + x, fine_win[1] + y, w, h))
    return out


def _refine(ai: Image.Image, cut: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """在粗定位框附近（不越过相邻商品）用商品自身纹理精搜位置与大小。"""
    W, H = ai.size
    x, y, w, h = box
    win = (max(0, x - w // 4), max(0, y - h // 8), min(W, x + w + w // 4), min(H, y + h + h // 8))
    heights = [int(h * (0.94 + 0.01 * i)) for i in range(13)]
    _, fx, fy, fw, fh = _best(ai.crop(win), cut, heights, 1)
    return win[0] + fx, win[1] + fy, fw, fh


def _outline_template(cut: Image.Image, h: int) -> tuple[np.ndarray, np.ndarray, int]:
    """只用商品外轮廓做模板（多款同形不同标签时，按形状找位置，不受标签文字干扰）。"""
    w = max(1, int(cut.width * h / cut.height))
    alpha = cut.getchannel("A").resize((w, h), Image.Resampling.BILINEAR)
    pad = 3
    sil = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    sil.paste(alpha, (pad, pad))
    te = _edges(sil)
    m = (np.asarray(sil.filter(ImageFilter.MaxFilter(5)), dtype=np.float32) > 20).astype(np.float32)
    inner = (np.asarray(sil.filter(ImageFilter.MinFilter(5)), dtype=np.float32) > 20).astype(np.float32)
    return te, m * (1 - inner), w + 2 * pad


def _lineup_anchors(ai: Image.Image, cut: Image.Image, heights: list[int], n: int,
                    boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """多支陈列：按商品外轮廓在横带里找 n 个不重叠的位置，按 x 排序，返回每支的粗定位框 (x, y, w, h)。"""
    W, H = ai.size
    top = max(0, min(b[1] for b in boxes) - int(max(b[3] for b in boxes) * 0.6))
    bottom = min(H, max(b[1] + b[3] for b in boxes) + int(max(b[3] for b in boxes) * 0.4))
    band = ai.crop((0, top, W, bottom))
    small = band.resize((max(1, W // COARSE_STEP), max(1, band.height // COARSE_STEP)), Image.Resampling.BILINEAR)
    e = _edges(small)
    best_map, best_w, best_h = None, 0, 0
    for h in heights:
        hs = max(8, h // COARSE_STEP)
        if hs >= e.shape[0]:
            continue
        te, m, ws = _outline_template(cut, hs)
        ssd = _ssd_map(e, te, m)
        if best_map is None or float(ssd.min()) < float(best_map.min()):
            best_map, best_w, best_h = ssd, ws, hs
    peaks: list[tuple[int, int]] = []
    ssd = best_map.copy()
    for _ in range(n):
        y, x = np.unravel_index(np.argmin(ssd), ssd.shape)
        peaks.append((int(x), int(y)))
        # 陈列是横向排开的：找到一支后整列排除，避免同一支在不同高度被重复命中
        ssd[:, max(0, x - int(best_w * 0.8)): x + int(best_w * 0.8) + 1] = np.inf
    peaks.sort()
    pad = 3
    found = []
    for x, y in peaks:
        X, Y = (x + pad) * COARSE_STEP, top + (y + pad) * COARSE_STEP
        w, h = (best_w - 2 * pad) * COARSE_STEP, (best_h) * COARSE_STEP
        found.append((max(0, X), max(0, Y), w, h))
    return found


# ---------- ④ 贴回 + 光色迁移 ----------
def relight(p: Image.Image, ai_patch: Image.Image, strength: float = 0.9) -> Image.Image:
    """把 AI 图里产品区域的低频光色（大范围明暗 + 色温）按 RGB 分通道迁移到原抠图；文字、边缘细节不变。"""
    r = max(3, p.width // 6)
    a = np.asarray(p.getchannel("A"), dtype=np.float32) / 255.0
    den = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r)),
                     dtype=np.float32) / 255.0

    def low(ch: np.ndarray) -> np.ndarray:
        num = Image.fromarray(np.clip(ch * a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r))
        return np.asarray(num, dtype=np.float32) / np.maximum(den, 1e-3)

    src = np.asarray(p.convert("RGB"), dtype=np.float32)
    ref = np.asarray(ai_patch.convert("RGB").resize(p.size), dtype=np.float32)
    out = np.empty_like(src)
    for c in range(3):
        ratio = np.clip(low(ref[..., c]) / np.maximum(low(src[..., c]), 1.0), 0.7, 1.15)
        out[..., c] = src[..., c] * (1 + strength * (ratio - 1))
    im = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    im.putalpha(p.getchannel("A"))
    return im


def plausible(found: list[tuple[int, int, int, int]], boxes: list[tuple[int, int, int, int]], size: tuple[int, int]) -> bool:
    """定位结果是否可信：大小与预期接近、彼此不重叠、都落在画面里。不可信时宁可不贴回。"""
    W, H = size
    exp_h = float(np.median([b[3] for b in boxes]))
    for x, y, w, h in found:
        if not (0.65 * exp_h <= h <= 1.5 * exp_h) or x < 0 or y < 0 or x + w > W or y + h > H:
            return False
    for i, a in enumerate(found):
        for b in found[i + 1:]:
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if ox > 0 and oy > 0 and ox * oy > 0.15 * min(a[2] * a[3], b[2] * b[3]):
                return False
    return True


def restore(ai: Image.Image, cutouts: list[Image.Image], found: list[tuple[int, int, int, int]]) -> Image.Image:
    """把原抠图按找到的位置贴回 AI 图，带光色迁移与 1px 羽化。"""
    canvas = ai.convert("RGBA")
    for cut, (x, y, w, h) in zip(cutouts, found, strict=True):
        p = cut.resize((max(1, w), max(1, h)), Image.Resampling.LANCZOS)
        p = relight(p, ai.crop((x, y, x + w, y + h)))
        p.putalpha(p.getchannel("A").filter(ImageFilter.GaussianBlur(0.6)))
        canvas.alpha_composite(p, (x, y))
    return canvas.convert("RGB")


# ---------- 白底主图（不调模型） ----------
def render_white(cutout: Image.Image, size: tuple[int, int], *, height: float = 0.72) -> Image.Image:
    """纯白底主图：产品居中，带柔和接地阴影（白底电商主图规范）。"""
    W, H = size
    canvas = Image.new("RGBA", size, (255, 255, 255, 255))
    h = int(H * height)
    w = int(cutout.width * h / cutout.height)
    if w > W * 0.8:
        w = int(W * 0.8)
        h = int(cutout.height * w / cutout.width)
    p = cutout.resize((w, h), Image.Resampling.LANCZOS)
    x, y = (W - w) // 2, (H - h) // 2 + int(H * 0.03)
    shadow = Image.new("L", size, 0)
    ew, eh = w * 1.1, max(6, w * 0.12)
    ImageDraw.Draw(shadow).ellipse((W / 2 - ew / 2, y + h - eh / 2, W / 2 + ew / 2, y + h + eh / 2), fill=70)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(4, w * 0.05)))
    canvas.paste((60, 50, 45, 255), (0, 0), shadow)
    canvas.alpha_composite(p, (x, y))
    return canvas.convert("RGB")
