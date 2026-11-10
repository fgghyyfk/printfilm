"""成分图：AI 一次出一张 2×2 成分示意四宫格，切成圆形小图，再按商品资料排成「圆图 + 成分名 + 说明」的详情屏。

参考常见详情页的成分屏：大标上方英文花字、标题、副标题，下面成分左右交错排列，底部「成分示意」声明。
只调一次生图（按一张计费）；成分少于 2 个时这张自动跳过。
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.services.ecom_set import typo

SLOT_KEY = "goods_ingredients_photo"
MAX_ITEMS = 4
MIN_ITEMS = 2
NOTE = "*以上为成分示意，不作为产品功效承诺"
DEFAULT_COPY = ("核心成分 温和养护", "", "Key Ingredients")


def ingredients_of(knowledge: dict | None) -> list[dict]:
    """资料里的成分（最多 4 个，去掉没有名字的）。"""
    rows = [r for r in (knowledge or {}).get("ingredients") or [] if str(r.get("name") or "").strip()]
    return rows[:MAX_ITEMS]


def available(knowledge: dict | None) -> bool:
    """至少两个成分才出这张。"""
    return len(ingredients_of(knowledge)) >= MIN_ITEMS


def grid_prompt(items: list[dict], *, palette: str = "") -> str:
    """四宫格成分示意摄影提示词：每格一个成分的写实微距，无文字。"""
    cells = []
    for i, r in enumerate(items[:MAX_ITEMS], 1):
        desc = str(r.get("desc") or "").strip()
        cells.append(f"第{i}格：{r['name']}" + (f"（{desc[:24]}）" if desc else ""))
    if len(cells) < 4:
        cells += [f"第{i}格：干净的浅色留白" for i in range(len(cells) + 1, 5)]
    tone = f"，整体色调：{palette}" if palette else ""
    return ("2×2 四宫格成分示意摄影，四格大小完全相同，格与格之间用细白缝分隔，每格一个主体居中、四周留白，"
            "柔和自然光、干净的浅色背景、写实微距质感" + tone + "。"
            "用成分的原料实物或常见示意形态表现：植物原料画新鲜饱满的植物本体（鲜绿叶片、花朵、根茎切片），不要枯萎发黄，"
            "化学 / 保湿成分画晶莹的液滴、凝胶、乳霜或透明质地。" + "；".join(cells) +
            "。画面中不要出现任何文字、字母、标签、包装或商品")


def crop_cells(grid: Image.Image, n: int) -> list[Image.Image]:
    """把四宫格切成 n 张正方形（每格中心 86%，去掉分隔白缝）。"""
    W, H = grid.size
    out = []
    for i in range(min(n, 4)):
        cx, cy = (i % 2) * W // 2, (i // 2) * H // 2
        cw, ch = W // 2, H // 2
        side = int(min(cw, ch) * 0.86)
        x0, y0 = cx + (cw - side) // 2, cy + (ch - side) // 2
        out.append(grid.crop((x0, y0, x0 + side, y0 + side)).convert("RGB"))
    return out


def _circle(img: Image.Image, cell: Image.Image, center: tuple[int, int], d: int) -> None:
    """圆形成分图（柔和投影 + 细描边）。"""
    c = cell.resize((d, d), Image.Resampling.LANCZOS)
    mask = Image.new("L", (d * 4, d * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d * 4 - 1, d * 4 - 1), fill=255)
    mask = mask.resize((d, d), Image.Resampling.LANCZOS)
    x, y = center[0] - d // 2, center[1] - d // 2
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((x + d * 0.02, y + d * 0.05, x + d * 1.02, y + d * 1.05), fill=(60, 45, 30, 60))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(d * 0.05)))
    img.paste(c, (x, y), mask)
    ImageDraw.Draw(img).ellipse((x, y, x + d, y + d), outline=(255, 255, 255, 230), width=max(2, d // 70))


def _desc_lines(desc: str, f, max_w: int) -> list[str]:
    """说明按标点断成短句，每句一行，最多 3 行。"""
    parts = [p.strip() for p in re.split(r"[，,。；;、\n]+", desc or "") if p.strip()]
    lines: list[str] = []
    for p in parts:
        lines += typo.wrap(p, f, 0.04, max_w, 2)
        if len(lines) >= 3:
            break
    return lines[:3]


def render(grid_path: Path, out: Path, *, size: tuple[int, int], copy: dict | None, styled: dict,
           type_style: str) -> Path:
    """排成分图并保存 JPEG。grid_path 为 AI 四宫格底图。"""
    from app.services.ecom_set.infoscreens import _clean, _header, _paper
    from app.services.ecom_set.scenes import bg_rgb

    W, H = size
    spec = ((styled.get("scene") or {}).get("spec")) or None
    img = Image.new("RGBA", (W, H), (*_paper(bg_rgb(spec) if spec else None), 255))
    style = typo.TYPE_STYLES[typo.normalize_type_style(type_style, "serif")]
    body = copy or {}
    y = _header(img, style, title=_clean(body.get("title") or DEFAULT_COPY[0]),
                subtitle=_clean(body.get("subtitle") or DEFAULT_COPY[1]), en=str(body.get("en") or DEFAULT_COPY[2]))
    items = ingredients_of(styled.get("knowledge"))
    with Image.open(grid_path) as g:
        cells = crop_cells(g.convert("RGB"), len(items))
    note_f = typo.font("sans", False, int(W * 0.022))
    bottom = int(H * 0.955) - typo.line_h(note_f, 0.4)
    n = max(1, len(cells))
    row_h = (bottom - y) / n
    d = int(min(W * 0.28, row_h * 0.8))
    # 每组（成分名 + 细线 + 说明）必须放进本行高度的 80%：放不下先减说明行数，再整体缩字号，避免上下两组贴在一起
    name_size, desc_size, max_lines = 0.038, 0.024, 3
    for _ in range(8):
        name_f = typo.font("serif" if style.id == "serif" else "sans", False, int(W * name_size))
        desc_f = typo.font("sans", False, int(W * desc_size))
        block = typo.line_h(name_f, 0.4) + int(W * 0.018) + typo.line_h(desc_f, 0.45) * max_lines
        if block <= row_h * 0.8:
            break
        if max_lines > 2:
            max_lines = 2
        else:
            name_size, desc_size = name_size * 0.92, desc_size * 0.92
    draw = ImageDraw.Draw(img)
    rule = (205, 196, 184)
    for i, (cell, row) in enumerate(zip(cells, items, strict=False)):
        cy = int(y + row_h * (i + 0.5))
        left = i % 2 == 0  # 圆图左右交错
        cx = int(W * 0.24) if left else int(W * 0.76)
        _circle(img, cell, (cx, cy), d)
        draw = ImageDraw.Draw(img)
        tx0 = int(W * 0.24 + d / 2 + W * 0.06) if left else int(W * 0.08)
        tx1 = int(W * 0.92) if left else int(W * 0.76 - d / 2 - W * 0.06)
        name = f"•{_clean(str(row['name']))}" if left else f"{_clean(str(row['name']))}•"
        lines = _desc_lines(_clean(str(row.get("desc") or "")), desc_f, tx1 - tx0)[:max_lines]
        block = typo.line_h(name_f, 0.4) + int(W * 0.018) + typo.line_h(desc_f, 0.45) * len(lines)
        ty = cy - block // 2
        nw = typo.text_width(name, name_f, 0.04)
        typo.draw_text(draw, (tx0 if left else tx1 - nw, ty), name, name_f, typo.INK, 0.04)
        ly = ty + typo.line_h(name_f, 0.4)
        draw.line([(tx0, ly), (tx1, ly)], fill=rule, width=max(1, W // 720))
        ly += int(W * 0.018)
        for line in lines:
            lw = typo.text_width(line, desc_f, 0.04)
            typo.draw_text(draw, (tx0 if left else tx1 - lw, ly), line, desc_f, typo.INK_MUTED, 0.04)
            ly += typo.line_h(desc_f, 0.45)
    nw = typo.text_width(NOTE, note_f, 0.02)
    typo.draw_text(draw, ((W - nw) / 2, int(H * 0.955) - note_f.size), NOTE, note_f, (150, 88, 72), 0.02)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, format="JPEG", quality=94)
    return out


async def generate_grid(db, ecom, item, info: dict, dest: Path) -> Path:
    """调一次生图出成分四宫格底图（按一张计费），返回本地路径。"""
    from app.services import storage
    from app.services.ark import get_ark
    from app.services.drama.billing_util import record_seedream_image_usage
    from app.services.ecom_set.jobs import NEGATIVE_PROMPT, engine_kwargs, usage_model
    from app.services.ecom_set.presets import GEN_SIZE
    from app.services.ecom_set.scenes import normalize_spec

    items = ingredients_of(info.get("knowledge"))
    if len(items) < MIN_ITEMS:
        raise RuntimeError("商品资料里的成分不足两个，无法生成成分图")
    spec = normalize_spec(((info.get("scene") or {}).get("spec")) or {})
    result = await get_ark().gen_image(grid_prompt(items, palette=str(spec.get("palette") or "")), NEGATIVE_PROMPT,
                                       None, project_id=0, shot_no=ecom.user_id, size=GEN_SIZE["1:1"],
                                       **engine_kwargs(ecom, item))
    await record_seedream_image_usage(
        db, user_id=ecom.user_id, model=usage_model(ecom, item), domain="studio", image_result=result,
        extra_raw={"tool_id": "ecomset", "set_id": ecom.id, "idx": item.idx, "mode": "ingredients"},
    )
    url = result.local_url or result.remote_url or ""
    if not url:
        raise RuntimeError("生图未返回图片")
    return await storage.ensure_local_media(url, dest)
