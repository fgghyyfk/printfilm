"""电商图「场景库」：场景卡结构、行业、提示词拼装、查询与快照。

一个场景 = 背景 + 主色调 + 台面 + 道具 + 光线 + 氛围（+ 产品占比 / 文案留白），系统场景、我的场景共用同一结构。
生成时把场景快照进套图（plan.scene），以后场景被修改也不影响历史套图。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_ecom import EcomScene

# 行业：id → 名称（首批 7 个）
INDUSTRIES: dict[str, str] = {
    "daily": "日化个护",
    "beauty": "美妆护肤",
    "food": "食品零食",
    "tea": "茶叶",
    "liquor": "酒类",
    "digital": "3C数码",
    "jewelry": "珠宝首饰",
}
# 品类默认打开的行业
CATEGORY_INDUSTRY: dict[str, str] = {"goods": "daily", "jewelry": "jewelry"}

SPEC_KEYS = ("backdrop", "palette", "surface", "props", "light", "mood")
DEFAULT_BG = "D8C3A8"
HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


def normalize_spec(raw: dict[str, Any] | None) -> dict[str, Any]:
    """规整场景字段：文字截断、道具列表、底色与产品占比钳到合理范围。"""
    body = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for k in ("backdrop", "palette", "surface", "light", "mood"):
        out[k] = str(body.get(k) or "").strip()[:80]
    props = body.get("props") or []
    if isinstance(props, str):
        props = [p for p in re.split(r"[，,、]", props)]
    out["props"] = [str(p).strip()[:20] for p in props if str(p).strip()][:8]
    color = str(body.get("bg_color") or DEFAULT_BG).strip().lstrip("#")
    out["bg_color"] = color.upper() if HEX_RE.match(color) else DEFAULT_BG
    try:
        ratio = float(body.get("product_ratio") or 0.5)
    except (TypeError, ValueError):
        ratio = 0.5
    out["product_ratio"] = min(0.7, max(0.3, ratio))
    blank = str(body.get("blank") or "top")
    out["blank"] = blank if blank in {"top", "bottom", "none"} else "top"
    out["prompt"] = str(body.get("prompt") or "").strip()[:600]
    return out


def scene_text(spec: dict[str, Any], *, with_props: bool = True) -> str:
    """场景卡 → 给生图模型的场景描述（有手写提示词时以手写为准）。with_props=False 时只给背景/台面/光线/色调骨架。"""
    s = normalize_spec(spec)
    if s["prompt"]:
        return s["prompt"]
    parts = []
    if s["backdrop"]:
        parts.append(f"背景：{s['backdrop']}")
    if s["palette"]:
        parts.append(f"主色调：{s['palette']}")
    if s["surface"]:
        parts.append(f"商品立在{s['surface']}上")
    if s["props"] and with_props:
        parts.append(f"周围点缀{'、'.join(s['props'])}，道具小巧、尺寸与商品相称、比例真实，不遮挡商品")
    if s["light"]:
        parts.append(f"光线：{s['light']}，商品在台面上有自然的柔和阴影")
    if s["mood"]:
        parts.append(f"氛围：{s['mood']}")
    return "场景设定——" + "；".join(parts) if parts else ""


def bg_rgb(spec: dict[str, Any]) -> tuple[int, int, int]:
    """摆位底图用的底色（接近场景主色，便于 AI 自然延展）。"""
    c = normalize_spec(spec)["bg_color"]
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def snapshot(row: EcomScene | None) -> dict | None:
    """把场景存进套图用的快照。"""
    if row is None:
        return None
    return {"key": row.key, "name": row.name, "industries": list(row.industries or []), "spec": normalize_spec(row.spec)}


def to_out(row: EcomScene) -> dict:
    """序列化给前端。"""
    return {
        "key": row.key, "name": row.name, "scope": row.scope, "industries": list(row.industries or []),
        "description": row.description or "", "cover_url": row.cover_url or "", "spec": normalize_spec(row.spec),
    }


async def list_scenes(db: AsyncSession, user_id: int, *, industry: str | None = None,
                      page: int = 1, page_size: int = 24) -> tuple[list[EcomScene], int]:
    """系统场景 + 我的场景（服务端分页，按行业筛选）。"""
    cond = [EcomScene.is_active.is_(True), or_(EcomScene.scope == "system", EcomScene.owner_user_id == user_id)]
    q = select(EcomScene).where(*cond)
    rows = list((await db.execute(q.order_by(EcomScene.sort_order, EcomScene.id))).scalars().all())
    if industry:
        rows = [r for r in rows if industry in (r.industries or [])]
    total = len(rows)
    page, page_size = max(1, page), min(max(1, page_size), 60)
    return rows[(page - 1) * page_size: page * page_size], total


async def get_scene(db: AsyncSession, user_id: int, key: str) -> EcomScene:
    """按 key 取一个可用场景（系统场景或自己的场景）。"""
    row = (
        await db.execute(
            select(EcomScene).where(
                EcomScene.key == key, EcomScene.is_active.is_(True),
                or_(EcomScene.scope == "system", EcomScene.owner_user_id == user_id),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ValueError("场景不存在或已下架")
    return row


def industries_out() -> list[dict]:
    """行业选项。"""
    return [{"id": k, "name": v} for k, v in INDUSTRIES.items()]


_SEEDED = False
COVER_DIR = "ecom_scenes"


def cover_path_for(key: str) -> str:
    """系统场景封面的本地静态路径（文件存在才返回）。"""
    from app.services.storage import STATIC_ROOT

    p = STATIC_ROOT / COVER_DIR / f"{key}.jpg"
    return f"/static/{COVER_DIR}/{key}.jpg" if p.exists() else ""


async def ensure_seeded() -> None:
    """建表（若缺）并插入缺失的系统场景；已有记录以后台为准，只补封面。每个进程只执行一次。"""
    global _SEEDED
    if _SEEDED:
        return
    from app.database import AsyncSessionLocal, engine
    from app.services.ecom_set.scenes_seed import SYSTEM_SCENES

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: EcomScene.__table__.create(sync, checkfirst=True))
    async with AsyncSessionLocal() as db:
        existing = {r.key: r for r in (await db.execute(select(EcomScene).where(EcomScene.scope == "system"))).scalars()}
        for order, item in enumerate(SYSTEM_SCENES):
            cover = cover_path_for(item["key"])
            row = existing.get(item["key"])
            if row is not None:
                if not row.cover_url and cover:
                    row.cover_url = cover
                continue
            db.add(EcomScene(
                key=item["key"], scope="system", name=item["name"], industries=item["industries"],
                description=item["description"], spec=normalize_spec(item["spec"]), cover_url=cover,
                sort_order=order * 10, is_active=True,
            ))
        await db.commit()
    _SEEDED = True
