"""日化/标品「信息屏」：多款一览、卖点详解、成分说明、使用方法、产品信息、品牌故事。

不调 AI，按商品资料（plan.styled.knowledge 快照）直接排版：免费、秒出、字清楚。
所有文字排版前过一遍广告法极限词库（替换模式）。
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw

from app.services.compliance.ad_words import check_text
from app.services.ecom_set import dims as dims_mod
from app.services.ecom_set import typo

# 槽位 key → 需要的资料（判断能否生成）
INFO_SLOTS = ("goods_info_variants", "goods_info_points", "goods_info_ingredients", "goods_info_usage",
              "goods_info_specs", "goods_info_story", "goods_info_size")
SIZE_SLOT = "goods_info_size"
DEFAULT_COPY = {
    "goods_info_variants": ("多款可选 一图看懂", "", "Collection"),
    "goods_info_points": ("为什么选它", "", "Highlights"),
    "goods_info_ingredients": ("成分说明", "", "Ingredients"),
    "goods_info_usage": ("使用方法", "", "How to use"),
    "goods_info_specs": ("产品信息", "", "Product details"),
    "goods_info_story": ("品牌故事", "", "Our story"),
    "goods_info_size": ("产品尺寸", "实物测量 仅供参考", "Size"),
}


def _clean(text: str) -> str:
    out, _ = check_text(str(text or ""))
    return out.strip()


def available(slot_key: str, k: dict, variants: list[dict], dims: dict | None = None) -> bool:
    """资料是否够生成这张信息屏（尺寸标注要有高或宽）。"""
    if slot_key == SIZE_SLOT:
        return bool({"h", "w"} & set(dims_mod.normalize(dims or {})))
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
    if slot_key == SIZE_SLOT:
        return "商品的实际尺寸（高或宽）"
    need = {"goods_info_variants": "至少两个款式（商品里多加几个颜色款/香型，或资料里的款式）",
            "goods_info_points": "至少两条卖点", "goods_info_ingredients": "成分", "goods_info_usage": "使用方法",
            "goods_info_story": "品牌故事"}.get(slot_key, "资料")
    return f"商品资料里的{need}"


# ---------- 画布与标题 ----------
def _paper(spec_bg: tuple[int, int, int] | None) -> tuple[int, int, int]:
    """信息屏底色：近白暖调，带一点场景主色（9:1 混合），和场景图衔接又足够干净。"""
    base = (251, 249, 246)
    if not spec_bg:
        return base
    return tuple(int(b * 0.9 + s * 0.1) for b, s in zip(base, spec_bg, strict=True))  # type: ignore[return-value]


RULE = (205, 196, 184)


def _title_family(style: typo.TypeStyle) -> str:
    return "sans" if style.id != "serif" else "serif"


def _header(img: Image.Image, style: typo.TypeStyle, *, title: str, subtitle: str, en: str) -> int:
    """英文小标（两侧细线）→ 大标题 → 副标题（两侧细线），返回下方内容起始 y。"""
    W, H = img.size
    d = ImageDraw.Draw(img)
    y = int(H * 0.045)
    inner = int(W * 0.84)

    def flanked(text: str, f, fill, track: float, y0: int) -> None:
        tw = typo.text_width(text, f, track)
        x = (W - tw) / 2
        typo.draw_text(d, (x, y0), text, f, fill, track)
        mid = y0 + f.size * 0.62
        gap, ln = W * 0.03, W * 0.12
        d.line([(x - gap - ln, mid), (x - gap, mid)], fill=RULE, width=max(1, W // 720))
        d.line([(x + tw + gap, mid), (x + tw + gap + ln, mid)], fill=RULE, width=max(1, W // 720))

    if en:
        # 典雅风格用英文花体（参考详情页大标上方的手写英文），其余衬线小字
        f = typo.en_font(style, int(W * (0.05 if style.en_script else 0.026)))
        flanked(typo.en_label(en, style) if style.en_script else en, f, typo.INK_MUTED, 0.0 if style.en_script else 0.04, y)
        y += typo.line_h(f, 0.3 if style.en_script else 0.5)
    if title:
        f, lines = typo.fit(title, _title_family(style), style.id == "bold", int(W * 0.07), 0.08, inner, 2)
        for line in lines:
            typo.draw_text(d, ((W - typo.text_width(line, f, 0.08)) / 2, y), line, f, typo.INK, 0.08)
            y += typo.line_h(f, 0.25)
    if subtitle:
        f, lines = typo.fit(subtitle, "sans", False, int(W * 0.03), 0.12, int(W * 0.6), 1)
        y += int(W * 0.008)
        flanked(lines[0], f, typo.INK_MUTED, 0.12, y)
        y += typo.line_h(f, 0.5)
    return y + int(H * 0.03)


def _footer(img: Image.Image, lines: list[str], *, title: str = "", flank: bool = False) -> int:
    """底部说明条（免责声明 / 适用场景 / 温馨提示），返回它占用区域的顶部 y。"""
    W, H = img.size
    lines = [_clean(x) for x in lines if x and _clean(x)]
    if not lines and not title:
        return int(H * 0.96)
    d = ImageDraw.Draw(img)
    f = typo.font("sans", False, int(W * 0.022))
    t_f = typo.font(_title_family(typo.TYPE_STYLES["serif"]), False, int(W * 0.03))
    width = int(W * 0.8)
    wrapped: list[str] = []
    for ln in lines[:4]:
        wrapped += typo.wrap(ln, f, 0.04, width, 2)
    h = len(wrapped) * typo.line_h(f, 0.6) + (typo.line_h(t_f, 0.5) if title else 0)
    top = int(H * 0.965) - h
    y = top
    if title:
        tw = typo.text_width(title, t_f, 0.2)
        typo.draw_text(d, ((W - tw) / 2, y), title, t_f, typo.INK, 0.2)
        y += typo.line_h(t_f, 0.5)
    for ln in wrapped:
        tw = typo.text_width(ln, f, 0.04)
        x = (W - tw) / 2
        typo.draw_text(d, (x, y), ln, f, typo.INK_MUTED if not flank else typo.INK, 0.04)
        if flank:
            mid = y + f.size * 0.62
            d.line([(W * 0.06, mid), (x - W * 0.03, mid)], fill=RULE, width=max(1, W // 720))
            d.line([(x + tw + W * 0.03, mid), (W * 0.94, mid)], fill=RULE, width=max(1, W // 720))
        y += typo.line_h(f, 0.6)
    rule_y = top - int(H * 0.02)
    if not flank:
        d.line([(W * 0.08, rule_y), (W * 0.92, rule_y)], fill=RULE, width=max(1, W // 720))
    return rule_y - int(H * 0.01)


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
    """款名显示：商品库里同编号的款有中文名时用它；否则把中文名后面拖的英文去掉。"""
    num = re.search(r"No\.?\s*(\d+)", name, re.I)
    if num:
        for v in variants:
            vn = str(v.get("name") or "")
            if re.search(rf"No\.?\s*0*{int(num.group(1))}\b", vn, re.I) and any("\u4e00" <= c <= "\u9fff" for c in vn):
                return vn
    if not any("\u4e00" <= ch <= "\u9fff" for ch in name):
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


def _variants(img: Image.Image, y: int, bottom: int, k: dict, variants: list[dict], style: typo.TypeStyle) -> None:
    """多款一览：有商品图的款排成网格（大图 + 款名 + 一行香调），没有图的款放到底部文字条。"""
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
    with_img = [r for r in rows if r["image"]][:9] or rows[:9]
    extra = [r for r in rows if r not in with_img][:3]

    def notes(r: dict) -> str:
        parts = [r.get(x) for x in ("top", "middle", "base") if r.get(x)]
        if parts:
            return " · ".join(p.split("、")[0] for p in parts)
        return r.get("desc") or ""

    if extra:
        lines = [f"{r['name']}　{_clean(' / '.join(x for x in (r.get('top'), r.get('middle'), r.get('base')) if x) or r.get('desc') or '')}"
                 for r in extra]
        bottom = min(bottom, _footer(img, lines))
    n = len(with_img)
    cols = 2 if n in (2, 4) else 3
    n_rows = (n + cols - 1) // cols
    margin = int(W * 0.06)
    cell_w = (W - 2 * margin) // cols
    cell_h = int((bottom - y) / max(1, n_rows))
    d = ImageDraw.Draw(img)
    name_f = typo.font(_title_family(style), False, int(W * (0.032 if cols == 2 else 0.027)))
    note_f = typo.font("sans", False, int(W * (0.023 if cols == 2 else 0.02)))
    text_h = typo.line_h(name_f, 0.35) + typo.line_h(note_f, 0.4) + int(cell_h * 0.06)
    img_h = max(int(cell_h * 0.3), cell_h - text_h)
    last_n = n - (n_rows - 1) * cols
    for i, r in enumerate(with_img):
        row_i = i // cols
        shift = (cols - last_n) * cell_w // 2 if row_i == n_rows - 1 else 0
        cx, cy = margin + shift + (i % cols) * cell_w, y + row_i * cell_h
        if r["image"]:
            _paste_cutout(img, r["image"], (cx + int(cell_w * 0.15), cy + int(cell_h * 0.03), cx + int(cell_w * 0.85), cy + img_h))
            d = ImageDraw.Draw(img)
        ty = cy + img_h + int(cell_h * 0.03)
        ty = _para(d, _clean(r["name"]), name_f, x=cx, y=ty, width=cell_w, fill=typo.INK, max_lines=1, center=True, gap=0.35)
        _para(d, _clean(notes(r)), note_f, x=cx + 8, y=ty, width=cell_w - 16, fill=typo.INK_MUTED, max_lines=1,
              center=True, gap=0.4)


def _side_list(img: Image.Image, y: int, bottom: int, rows: list[tuple[str, str]], style: typo.TypeStyle, *,
               product: str, numbered: bool) -> None:
    """左侧大商品图 + 右侧条目（标题 + 说明，细线分隔）；放不下自动缩小字号，整块垂直居中。"""
    W, H = img.size
    avail = max(1, bottom - y)
    px0, px1 = int(W * 0.05), int(W * 0.40)
    if product:
        _paste_cutout(img, product, (px0, y + int(avail * 0.06), px1, y + int(avail * 0.94)))
    x0 = int(W * 0.45) if product else int(W * 0.12)
    width = int(W * 0.94) - x0
    big = 0.044
    for _ in range(10):
        layer, used = _draw_rows(rows, style, W=W, avail=avail, big=big, numbered=numbered, x0=x0, width=width)
        if used <= avail:
            break
        big *= 0.9
    used = min(used, avail)
    img.alpha_composite(layer.crop((0, 0, W, max(1, used))), (0, y + max(0, (avail - used) // 2)))


def _draw_rows(rows: list[tuple[str, str]], style: typo.TypeStyle, *, W: int, avail: int, big: float,
               numbered: bool, x0: int, width: int) -> tuple[Image.Image, int]:
    """把条目画到透明图层，返回 (图层, 用掉的高度)。"""
    t_f = typo.font(_title_family(style), style.id == "bold", int(W * big))
    s_f = typo.font("sans", False, int(W * big * 0.6))
    n_f = typo.font("serif", False, int(W * big * 1.25))
    gap = int(W * big * 0.75)
    layer = Image.new("RGBA", (W, max(1, avail * 2)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tx = x0 + (int(n_f.size * 1.6) if numbered else 0)
    tw = width - (tx - x0)
    ly = 0
    for i, (title, desc) in enumerate(rows):
        top = ly
        if numbered:
            typo.draw_text(d, (x0, ly - int(n_f.size * 0.18)), f"{i + 1:02d}", n_f, typo.INK_MUTED, 0.0)
        if not title:
            l_f = typo.font(_title_family(style), False, int(W * big * 0.68))
            ly = _para(d, _clean(desc), l_f, x=tx, y=ly, width=tw, fill=typo.INK, max_lines=4, gap=0.45)
            desc = ""
        else:
            ly = _para(d, _clean(title), t_f, x=tx, y=ly, width=tw, fill=typo.INK, max_lines=2, gap=0.3)
        if desc:
            ly = _para(d, _clean(desc), s_f, x=tx, y=ly + int(W * 0.004), width=tw, fill=typo.INK_MUTED,
                       max_lines=2, gap=0.5)
        ly = max(ly, top + int(n_f.size * 1.1)) + gap // 2
        if i < len(rows) - 1:
            d.line([(x0, ly), (x0 + width, ly)], fill=RULE, width=max(1, W // 720))
            ly += gap // 2 + gap // 3
    return layer, ly


def _specs(img: Image.Image, y: int, bottom: int, rows: list[tuple[str, str]], style: typo.TypeStyle, product: str) -> None:
    """左侧商品图 + 右侧信息表（项目 | 内容，细线分隔）。"""
    W, H = img.size
    avail = max(1, bottom - y)
    _paste_cutout(img, product, (int(W * 0.05), y + int(avail * 0.08), int(W * 0.38), y + int(avail * 0.92)))
    x0, x1 = int(W * 0.43), int(W * 0.94)
    each = min(int(W * 0.085), int(avail * 0.92 / max(1, len(rows))))
    l_f = typo.font("sans", False, int(each * 0.34))
    v_f = typo.font(_title_family(style), False, int(each * 0.36))
    label_w = int((x1 - x0) * 0.3)
    d = ImageDraw.Draw(img)
    ty = y + (avail - each * len(rows)) // 2
    for i, (label, value) in enumerate(rows):
        typo.draw_text(d, (x0, ty + each * 0.28), _clean(label), l_f, typo.INK_MUTED, 0.1)
        _para(d, _clean(value), v_f, x=x0 + label_w, y=int(ty + each * 0.24), width=x1 - x0 - label_w,
              fill=typo.INK, max_lines=2 if each > v_f.size * 2.6 else 1, gap=0.25)
        ty += each
        if i < len(rows) - 1:
            d.line([(x0, ty), (x1, ty)], fill=RULE, width=max(1, W // 720))


def _story(img: Image.Image, y: int, bottom: int, k: dict, style: typo.TypeStyle, product: str) -> None:
    """品牌故事：一段居中正文，下方商品图。"""
    W, H = img.size
    margin = int(W * 0.12)
    d = ImageDraw.Draw(img)
    f = typo.font(_title_family(style), False, int(W * 0.03))
    y = _para(d, _clean(k.get("story") or ""), f, x=margin, y=y + int(H * 0.01), width=W - 2 * margin, fill=typo.INK,
              gap=0.9, max_lines=9, center=True)
    top = max(y + int(H * 0.04), int(H * 0.55))
    if top < bottom - H * 0.12:
        _paste_cutout(img, product, (margin, top, W - margin, bottom))


def split_step(text: str) -> tuple[str, str]:
    """长步骤拆成「短标题 + 说明」：在第一个逗号处断开（标题 ≤12 字时）。"""
    text = (text or "").strip()
    m = re.match(r"^(.{2,12}?)[，,；;：:]\s*(.+)$", text)
    if len(text) > 12 and m:
        return m.group(1), m.group(2)
    if len(text) > 12:
        return "", text  # 拆不开的长句：整句用小一号字、最多 3 行
    return text, ""


INGREDIENT_NOTE = "以上为成分说明，不作为产品功效承诺"


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
    y = _header(img, style, title=_clean(body.get("title") or d_title), subtitle=_clean(body.get("subtitle") or d_sub),
                en=str(body.get("en") or d_en))
    k = styled.get("knowledge") or {}
    variants = list(styled.get("variants") or [])
    product = str(styled.get("product_path") or "")
    bottom = int(H * 0.95)
    if slot_key == "goods_info_variants":
        _variants(img, y, bottom, k, variants, style)
    elif slot_key == "goods_info_points":
        _side_list(img, y, bottom, [(r["title"], r.get("desc", "")) for r in k.get("selling_points") or []][:5], style,
                   product=product, numbered=False)
    elif slot_key == "goods_info_ingredients":
        cautions = [c for c in k.get("cautions") or [] if "成分" in c or "功效" in c]
        bottom = _footer(img, cautions[:1] or [INGREDIENT_NOTE])
        _side_list(img, y, bottom, [(r["name"], r.get("desc", "")) for r in k.get("ingredients") or []][:5], style,
                   product=product, numbered=False)
    elif slot_key == "goods_info_usage":
        scen = [s for s in k.get("scenarios") or []][:4]
        if scen:
            bottom = _footer(img, [" · ".join(scen)], flank=True)
        _side_list(img, y, bottom, [split_step(s) for s in k.get("usage") or []][:5], style, product=product, numbered=True)
    elif slot_key == "goods_info_specs":
        tips = [c for c in k.get("cautions") or [] if not ("成分" in c and "功效" in c)][:3]
        if tips:
            bottom = _footer(img, tips, title="温馨提示")
        _specs(img, y, bottom, spec_rows(styled, with_cautions=not tips), style, product)
    elif slot_key == "goods_info_story":
        _story(img, y, bottom, k, style, product)
    elif slot_key == SIZE_SLOT:
        from app.services.ecom_set.sizescreen import draw_size

        draw_size(img, y, bottom, styled.get("dims") or {}, product, style)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, format="JPEG", quality=94)
    return out


def spec_rows(styled: dict, *, with_cautions: bool = True) -> list[tuple[str, str]]:
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
             ("尺寸", dims_mod.short(styled.get("dims"))),
             ("包装", styled.get("material_label") or "")]
    values = {v for _, v in rows} | seen_vals
    for label, value in extra:
        if value and value not in values and label not in have and not any(label in h for h in have):
            rows.append((label, value))
    if with_cautions and k.get("cautions"):
        rows.append(("注意事项", "；".join(k["cautions"])[:30]))
    return rows[:9]
