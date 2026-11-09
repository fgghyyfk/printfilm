"""日化/标品「信息屏」：多款一览、卖点详解、成分说明、使用方法、产品信息、品牌故事。

不调 AI，按商品资料（plan.styled.knowledge 快照）直接排版：免费、秒出、字清楚。
所有文字排版前过一遍广告法极限词库（替换模式）。
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw

from app.services.compliance.ad_words import check_text
from app.services.ecom_set import typo

# 槽位 key → 需要的资料（判断能否生成）
INFO_SLOTS = ("goods_info_variants", "goods_info_points", "goods_info_ingredients", "goods_info_usage",
              "goods_info_specs", "goods_info_story")
DEFAULT_COPY = {
    "goods_info_variants": ("多款可选 一图看懂", "", "Collection"),
    "goods_info_points": ("为什么选它", "", "Highlights"),
    "goods_info_ingredients": ("成分说明", "", "Ingredients"),
    "goods_info_usage": ("使用方法", "", "How to use"),
    "goods_info_specs": ("产品信息", "", "Product details"),
    "goods_info_story": ("品牌故事", "", "Our story"),
}


def _clean(text: str) -> str:
    out, _ = check_text(str(text or ""))
    return out.strip()


def available(slot_key: str, k: dict, variants: list[dict]) -> bool:
    """资料是否够生成这张信息屏。"""
    if slot_key == "goods_info_variants":
        return len(k.get("variants") or []) >= 2 or len(variants) >= 2
    if slot_key == "goods_info_points":
        return len(k.get("selling_points") or []) >= 2
    if slot_key == "goods_info_ingredients":
        return bool(k.get("ingredients"))
    if slot_key == "goods_info_usage":
        return bool(k.get("usage"))
    if slot_key == "goods_info_story":
        return bool(k.get("story"))
    return True  # 产品信息：至少有品名与规格


def missing_hint(slot_key: str) -> str:
    """缺资料时给用户的提示。"""
    need = {"goods_info_variants": "至少两个款式（商品里多加几个颜色款/香型，或资料里的款式）",
            "goods_info_points": "至少两条卖点", "goods_info_ingredients": "成分", "goods_info_usage": "使用方法",
            "goods_info_story": "品牌故事"}.get(slot_key, "资料")
    return need


# ---------- 画布与标题 ----------
def _paper(spec_bg: tuple[int, int, int] | None) -> tuple[int, int, int]:
    """信息屏底色：场景主色和暖白按 1:4 混合，浅而不白。"""
    base = (248, 245, 240)
    if not spec_bg:
        return base
    return tuple(int(b * 0.8 + s * 0.2) for b, s in zip(base, spec_bg, strict=True))  # type: ignore[return-value]


def _header(img: Image.Image, style: typo.TypeStyle, *, title: str, subtitle: str, en: str) -> int:
    """画英文小标 + 标题 + 细线 + 副标题，返回下方内容起始 y。"""
    W, H = img.size
    d = ImageDraw.Draw(img)
    y = int(H * 0.06)
    margin = int(W * 0.08)
    inner = W - 2 * margin
    if en:
        f = typo.font("serif", False, int(W * 0.022))
        t = en.upper()
        typo.draw_text(d, ((W - typo.text_width(t, f, 0.32)) / 2, y), t, f, typo.INK_MUTED, 0.32)
        y += typo.line_h(f, 0.6)
    if title:
        f, lines = typo.fit(title, style.title_family if style.id != "bold" else "sans", style.id == "bold",
                            int(W * 0.06), 0.06, inner, 2)
        for line in lines:
            typo.draw_text(d, ((W - typo.text_width(line, f, 0.06)) / 2, y), line, f, typo.INK, 0.06)
            y += typo.line_h(f, 0.3)
    y += int(W * 0.012)
    rw = int(W * 0.05)
    d.line([((W - rw) / 2, y), ((W + rw) / 2, y)], fill=typo.INK_MUTED, width=max(1, W // 700))
    y += int(W * 0.03)
    if subtitle:
        f, lines = typo.fit(subtitle, "sans", False, int(W * 0.028), 0.1, inner, 2)
        for line in lines:
            typo.draw_text(d, ((W - typo.text_width(line, f, 0.1)) / 2, y), line, f, typo.INK_MUTED, 0.1)
            y += typo.line_h(f, 0.5)
    return y + int(H * 0.03)


def _paste_cutout(img: Image.Image, path: str, box: tuple[int, int, int, int]) -> None:
    """把商品抠图等比放进 box（底部居中，带浅接地阴影）。"""
    from app.services.ecom_set.composite import flatten_white, has_alpha, load_cutout

    p = Path(path or "")
    if not p.exists():
        return
    cut = load_cutout(p) if has_alpha(p) else flatten_white(p).convert("RGBA")
    x0, y0, x1, y1 = box
    scale = min((x1 - x0) / cut.width, (y1 - y0) / cut.height)
    w, h = max(1, int(cut.width * scale)), max(1, int(cut.height * scale))
    cut = cut.resize((w, h), Image.Resampling.LANCZOS)
    x, y = x0 + (x1 - x0 - w) // 2, y1 - h
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((x + w * 0.05, y1 - h * 0.025, x + w * 0.95, y1 + h * 0.025), fill=(60, 50, 40, 50))
    img.alpha_composite(shadow)
    img.alpha_composite(cut, (x, y))


def _para(d: ImageDraw.ImageDraw, text: str, f, *, x: int, y: int, width: int, fill, gap: float = 0.6,
          max_lines: int = 3, center: bool = False) -> int:
    """画一段折行文字，返回下方 y。"""
    for line in typo.wrap(text, f, 0.02, width, max_lines):
        lx = x + (width - typo.text_width(line, f, 0.02)) / 2 if center else x
        typo.draw_text(d, (lx, y), line, f, fill, 0.02)
        y += typo.line_h(f, gap)
    return y


# ---------- 各类信息屏 ----------
def display_name(name: str, variants: list[dict]) -> str:
    """款名显示：资料里只有英文名时用商品库里同编号的中文款名；中文名后面拖的英文去掉。"""
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in name)
    if not has_cjk:
        num = re.search(r"No\.?\s*(\d+)", name, re.I)
        for v in variants:
            vn = str(v.get("name") or "")
            if num and re.search(rf"No\.?\s*0*{int(num.group(1))}\b", vn, re.I) and any("\u4e00" <= c <= "\u9fff" for c in vn):
                return vn
        return name
    m = re.match(r"^(.*?[\u4e00-\u9fff]+)\s+[A-Za-z].*$", name)
    return m.group(1) if m else name


def _match_image(name: str, variants: list[dict]) -> str:
    """按编号或名称把资料里的款式对应到商品库颜色款的图片。"""
    num = re.search(r"No\.?\s*(\d+)", name, re.I)
    for v in variants:
        vn = str(v.get("name") or "")
        if num and re.search(rf"No\.?\s*0*{int(num.group(1))}\b", vn, re.I):
            return str(v.get("image_path") or "")
        if vn and (vn in name or name in vn):
            return str(v.get("image_path") or "")
    return ""


def _variants(img: Image.Image, y: int, k: dict, variants: list[dict], style: typo.TypeStyle) -> None:
    W, H = img.size
    rows = [dict(r) for r in k.get("variants") or []]
    if len(rows) < 2:
        rows = [{"name": v.get("name") or "", "top": "", "middle": "", "base": "", "desc": ""} for v in variants]
    for r in rows:
        r["image"] = _match_image(r["name"], variants)
        r["name"] = display_name(r["name"], variants)
    if not any(r["image"] for r in rows) and len(rows) == len(variants):
        for r, v in zip(rows, variants, strict=True):
            r["image"] = str(v.get("image_path") or "")
    rows = rows[:9]
    cols = 2 if len(rows) in (2, 4) else 3
    n_rows = (len(rows) + cols - 1) // cols
    margin = int(W * 0.06)
    cell_w = (W - 2 * margin) // cols
    cell_h = min(int((H * 0.95 - y) / max(1, n_rows)), int(cell_w * 1.9))
    y += max(0, int((H * 0.95 - y - cell_h * n_rows) / 2))
    d = ImageDraw.Draw(img)
    name_f = typo.font(style.title_family if style.id != "bold" else "sans", False, int(W * (0.03 if cols == 2 else 0.026)))
    note_f = typo.font("sans", False, int(W * (0.021 if cols == 2 else 0.018)))
    last_n = len(rows) - (n_rows - 1) * cols

    # 先算每格文字占多高，全部格子用同一个商品图高度（按最高的文字算），避免与下一行重叠、同行高低不齐
    def text_h(r: dict) -> int:
        name_lines = len(typo.wrap(_clean(r["name"]), name_f, 0.02, cell_w, 2))
        notes = sum(1 for x in ("top", "middle", "base") if r.get(x)) or (2 if r.get("desc") else 0)
        return (name_lines * typo.line_h(name_f, 0.3) + notes * typo.line_h(note_f, 0.45)
                + int(cell_h * 0.04) + int(cell_h * 0.08))
    img_h = max(int(cell_h * 0.25), cell_h - max(text_h(r) for r in rows))
    for i, r in enumerate(rows):
        row_i = i // cols
        # 最后一行不满时居中
        shift = (cols - last_n) * cell_w // 2 if row_i == n_rows - 1 else 0
        cx, cy = margin + shift + (i % cols) * cell_w, y + row_i * cell_h
        if r["image"]:
            _paste_cutout(img, r["image"], (cx + int(cell_w * 0.1), cy, cx + int(cell_w * 0.9), cy + img_h))
            d = ImageDraw.Draw(img)
        ty = cy + img_h + int(cell_h * 0.04)
        ty = _para(d, _clean(r["name"]), name_f, x=cx, y=ty, width=cell_w, fill=typo.INK, max_lines=2, center=True,
                   gap=0.3)
        for label, key in (("前调", "top"), ("中调", "middle"), ("后调", "base")):
            if r.get(key):
                ty = _para(d, f"{label} · {_clean(r[key])}", note_f, x=cx + 6, y=ty, width=cell_w - 12,
                           fill=typo.INK_MUTED, max_lines=1, center=True, gap=0.45)
        if r.get("desc") and not any(r.get(x) for x in ("top", "middle", "base")):
            _para(d, _clean(r["desc"]), note_f, x=cx + 6, y=ty, width=cell_w - 12, fill=typo.INK_MUTED,
                  max_lines=2, center=True, gap=0.45)


def _list(img: Image.Image, y: int, rows: list[tuple[str, str]], style: typo.TypeStyle, *, numbered: bool,
          product: str = "") -> None:
    """编号列表（卖点 / 成分 / 用法）：左侧序号，右侧标题 + 说明，行间细线；整块在剩余空间里垂直居中。"""
    W, H = img.size
    margin = int(W * 0.1)
    if product:
        ph = int(H * 0.22)
        _paste_cutout(img, product, (margin, y, W - margin, y + ph))
        y += ph + int(H * 0.035)
    avail = int(H * 0.95 - y)
    each = avail / max(1, len(rows))
    big = min(0.038, each / W * 0.42)
    text_x = margin + int(W * 0.12) if numbered else margin
    width = W - text_x - margin
    # 放不下就缩小字号重排，直到整块装进剩余空间
    for _ in range(8):
        layer, ly = _draw_list_rows(rows, style, W=W, avail=avail, each=each, big=big, numbered=numbered,
                                    margin=margin, text_x=text_x, width=width)
        if ly <= avail:
            break
        big *= 0.88
    used = min(ly, avail)
    img.alpha_composite(layer.crop((0, 0, W, max(1, used))), (0, y + max(0, (avail - used) // 2)))


def _draw_list_rows(rows: list[tuple[str, str]], style: typo.TypeStyle, *, W: int, avail: int, each: float, big: float,
                    numbered: bool, margin: int, text_x: int, width: int) -> tuple[Image.Image, int]:
    """把列表画到透明图层上，返回 (图层, 实际用掉的高度)；图层留两倍高度以便量出是否超出。"""
    num_f = typo.font("serif", False, int(W * big * 1.25))
    t_f = typo.font(style.title_family if style.id != "bold" else "sans", style.id == "bold", int(W * big))
    s_f = typo.font("sans", False, int(W * big * 0.72))
    row_gap = int(min(each * 0.18, W * 0.05, W * big * 1.1))
    layer = Image.new("RGBA", (W, max(1, avail * 2)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ly = 0
    for i, (title, desc) in enumerate(rows):
        top = ly
        if numbered:
            typo.draw_text(d, (margin, ly - int(num_f.size * 0.1)), f"{i + 1:02d}", num_f, typo.INK_MUTED, 0.02)
        ly = _para(d, _clean(title), t_f, x=text_x, y=ly, width=width, fill=typo.INK, max_lines=2, gap=0.35)
        if desc:
            ly = _para(d, _clean(desc), s_f, x=text_x, y=ly + int(W * 0.004), width=width, fill=typo.INK_MUTED,
                       max_lines=2, gap=0.55)
        ly = max(ly, top + int(num_f.size * 1.2)) + row_gap
        if i < len(rows) - 1:
            d.line([(text_x, ly - row_gap // 2), (W - margin, ly - row_gap // 2)], fill=(214, 207, 198), width=1)
            ly += row_gap // 2
    return layer, ly


def _specs(img: Image.Image, y: int, rows: list[tuple[str, str]], style: typo.TypeStyle, product: str) -> None:
    W, H = img.size
    margin = int(W * 0.12)
    ph = int(H * 0.3)
    _paste_cutout(img, product, (margin, y, W - margin, y + ph))
    y += ph + int(H * 0.05)
    d = ImageDraw.Draw(img)
    each = min(int(W * 0.075), int((H * 0.95 - y) / max(1, len(rows))))
    l_f = typo.font("sans", False, int(each * 0.36))
    v_f = typo.font(style.title_family if style.id != "bold" else "sans", False, int(each * 0.4))
    label_w = int((W - 2 * margin) * 0.3)
    for label, value in rows:
        d.line([(margin, y), (W - margin, y)], fill=(214, 207, 198), width=1)
        typo.draw_text(d, (margin, y + each * 0.28), _clean(label), l_f, typo.INK_MUTED, 0.1)
        _para(d, _clean(value), v_f, x=margin + label_w, y=int(y + each * 0.25), width=W - 2 * margin - label_w,
              fill=typo.INK, max_lines=1)
        y += each
    d.line([(margin, y), (W - margin, y)], fill=(214, 207, 198), width=1)


def _story(img: Image.Image, y: int, k: dict, style: typo.TypeStyle, product: str) -> None:
    W, H = img.size
    margin = int(W * 0.12)
    d = ImageDraw.Draw(img)
    f = typo.font(style.title_family if style.id != "bold" else "sans", False, int(W * 0.028))
    y = _para(d, _clean(k.get("story") or ""), f, x=margin, y=y + int(H * 0.02), width=W - 2 * margin, fill=typo.INK, gap=0.9,
              max_lines=9, center=True)
    top = max(y + int(H * 0.05), int(H * 0.55))
    if top < H * 0.82:
        _paste_cutout(img, product, (margin, top, W - margin, int(H * 0.92)))


def render_info(slot_key: str, out: Path, *, size: tuple[int, int], copy: dict | None, styled: dict,
                type_style: str) -> Path:
    """排一张信息屏并保存为 JPEG。styled 需含 knowledge / variants / product_path（建套图时快照）。"""
    from app.services.ecom_set.scenes import bg_rgb

    W, H = size
    spec = ((styled.get("scene") or {}).get("spec")) or None
    img = Image.new("RGBA", (W, H), (*_paper(bg_rgb(spec) if spec else None), 255))
    style = typo.TYPE_STYLES[typo.normalize_type_style(type_style, "serif")]
    body = copy or {}
    d_title, d_sub, d_en = DEFAULT_COPY.get(slot_key, ("", "", ""))
    title = _clean(body.get("title") or d_title)
    y = _header(img, style, title=title, subtitle=_clean(body.get("subtitle") or d_sub),
                en=str(body.get("en") or d_en))
    k = styled.get("knowledge") or {}
    variants = list(styled.get("variants") or [])
    product = str(styled.get("product_path") or "")
    if slot_key == "goods_info_variants":
        _variants(img, y, k, variants, style)
    elif slot_key == "goods_info_points":
        _list(img, y, [(r["title"], r.get("desc", "")) for r in k.get("selling_points") or []][:6], style,
              numbered=True)
    elif slot_key == "goods_info_ingredients":
        _list(img, y, [(r["name"], r.get("desc", "")) for r in k.get("ingredients") or []][:6], style,
              numbered=False, product=product)
    elif slot_key == "goods_info_usage":
        _list(img, y, [(s, "") for s in k.get("usage") or []][:6], style, numbered=True, product=product)
    elif slot_key == "goods_info_specs":
        _specs(img, y, spec_rows(styled), style, product)
    elif slot_key == "goods_info_story":
        _story(img, y, k, style, product)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, format="JPEG", quality=94)
    return out


def spec_rows(styled: dict) -> list[tuple[str, str]]:
    """产品信息表：资料里的规格优先，缺的用品名、规格、包装形态补。"""
    k = styled.get("knowledge") or {}
    rows: list[tuple[str, str]] = []
    seen_vals: set[str] = set()
    for r in k.get("specs") or []:
        value = str(r.get("value") or "")
        lead = re.match(r"^\s*([\d.]+\s*[A-Za-z\u4e00-\u9fff]{0,3})", value)
        key = lead.group(1).replace(" ", "").lower() if lead else value
        if value and key not in seen_vals:
            seen_vals.add(key)
            rows.append((r["label"], value))
    have = {label for label, _ in rows}
    extra = [("品名", styled.get("product_name") or ""), ("规格", styled.get("size") or ""),
             ("包装", styled.get("material_label") or "")]
    values = {v for _, v in rows} | seen_vals
    for label, value in extra:
        if value and value not in values and label not in have and not any(label in h for h in have):
            rows.append((label, value))
    if k.get("cautions"):
        rows.append(("注意事项", "；".join(k["cautions"])[:30]))
    return rows[:9]
