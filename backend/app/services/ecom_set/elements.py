"""电商图元素库：系统预置 + 我的元素（文字或实物照片）、按商品资料推荐、建套图时快照进套图。

元素只决定「画面里放什么」：场景卡定背景、台面、光线与色调，每张图再从选中的元素里挑 2～3 个不同组合。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_ecom import EcomElement
from app.services.ecom_set.elements_seed import CATEGORIES, system_elements
from app.services.ecom_set.prompts import clip, sanitize_scene

MAX_USER_ELEMENTS = 200
MAX_PICK = 12
_SEEDED = False


async def ensure_seeded(db: AsyncSession | None = None) -> None:
    """建表（若缺）并补齐系统元素；已有记录以库为准。每个进程只执行一次。

    传入 db 时用同一个会话建表与写入（请求内调用、测试库都适用），否则自开会话（启动时）。
    """
    global _SEEDED
    if _SEEDED:
        return
    if db is None:
        from app.database import AsyncSessionLocal

        async with AsyncSessionLocal() as own:
            await ensure_seeded(own)
            await own.commit()
        return
    await db.run_sync(lambda s: EcomElement.__table__.create(s.connection(), checkfirst=True))
    have = set((await db.execute(select(EcomElement.key).where(EcomElement.scope == "system"))).scalars())
    for item in system_elements():
        if item["key"] not in have:
            db.add(EcomElement(scope="system", is_active=True, **item))
    await db.flush()
    _SEEDED = True


def categories_out() -> list[dict]:
    return [{"id": k, "name": v} for k, v in CATEGORIES.items()]


def to_out(row: EcomElement) -> dict:
    """序列化给前端（不含本地路径）。"""
    return {"key": row.key, "name": row.name, "category": row.category, "scope": row.scope,
            "prompt": row.prompt, "scale": row.scale, "image_url": row.image_url or "",
            "industries": list(row.industries or [])}


def _visible(user: User):
    return or_(EcomElement.scope == "system", EcomElement.owner_user_id == user.id)


async def list_elements(db: AsyncSession, user: User, *, category: str = "", q: str = "", industry: str = "",
                        mine: bool = False, page: int = 1, page_size: int = 60) -> tuple[list[EcomElement], int]:
    """元素列表（服务端分页）：我的元素在前，再按分类、排序。"""
    await ensure_seeded(db)
    page, page_size = max(1, page), min(max(1, page_size), 400)
    base = select(EcomElement).where(EcomElement.is_active.is_(True), _visible(user))
    if mine:
        base = base.where(EcomElement.scope == "user")
    if category in CATEGORIES:
        base = base.where(EcomElement.category == category)
    if q.strip():
        like = f"%{q.strip()[:20]}%"
        base = base.where(or_(EcomElement.name.ilike(like), EcomElement.prompt.ilike(like)))
    rows = list((await db.execute(base.order_by(EcomElement.scope.desc(), EcomElement.category,
                                                EcomElement.sort_order, EcomElement.id))).scalars())
    if industry:
        rows = [r for r in rows if r.scope == "user" or not r.industries or industry in (r.industries or [])]
    total = len(rows)
    return rows[(page - 1) * page_size: page * page_size], total


async def create_element(db: AsyncSession, user: User, *, name: str, category: str, prompt: str,
                         image: tuple[bytes, str] | None) -> EcomElement:
    """新建我的元素：一句话描述，可带实物照片（生成时作为参考图）。"""
    await ensure_seeded(db)
    name = clip(name, 32)
    if not name:
        raise ValueError("请填写元素名称")
    count = int((await db.execute(select(func.count()).select_from(EcomElement)
                                  .where(EcomElement.owner_user_id == user.id))).scalar_one())
    if count >= MAX_USER_ELEMENTS:
        raise ValueError(f"我的元素最多 {MAX_USER_ELEMENTS} 个")
    text, _ = sanitize_scene(clip(prompt, 200) or name)
    row = EcomElement(key=f"u{user.id}_{uuid.uuid4().hex[:10]}", scope="user", owner_user_id=user.id, name=name,
                      category=category if category in CATEGORIES else "vessel", prompt=text or name,
                      keywords=[name], industries=[], scale="s", is_active=True)
    if image is not None:
        from app.services.ecom_set.jobs import public_url
        from app.services.ecom_set.service import save_product_image

        path = await asyncio.to_thread(save_product_image, user.id, *image)
        row.image_path, row.image_url = str(path), await asyncio.to_thread(public_url, path)
    db.add(row)
    await db.commit()
    return row


async def delete_element(db: AsyncSession, user: User, key: str) -> None:
    """删除我的元素（已生成的套图不受影响）。"""
    row = (await db.execute(select(EcomElement).where(EcomElement.key == key))).scalar_one_or_none()
    if row is None or row.owner_user_id != user.id:
        raise LookupError("元素不存在")
    await db.delete(row)
    await db.commit()


async def get_many(db: AsyncSession, user: User, keys: list[str], *, limit: int | None = MAX_PICK) -> list[EcomElement]:
    """按 key 取多个可见元素（保持传入顺序，忽略不存在的）；limit=None 不截断（元素包用）。"""
    keys = list(dict.fromkeys(k for k in keys if k))[:limit]
    if not keys:
        return []
    await ensure_seeded(db)
    rows = {r.key: r for r in (await db.execute(select(EcomElement).where(EcomElement.key.in_(keys), _visible(user)))).scalars()}
    return [rows[k] for k in keys if k in rows]


def snapshot(rows: list[EcomElement]) -> list[dict]:
    """存进套图的元素快照（含实物照本地路径，供生成时作参考图）。"""
    return [{"key": r.key, "name": r.name, "category": r.category, "prompt": r.prompt,
             "image_url": r.image_url or "", "image_path": r.image_path or ""} for r in rows]


def _knowledge_words(knowledge: dict | None) -> list[str]:
    """从商品资料里取香调、成分、款名等词，用于匹配元素关键词。"""
    k = knowledge or {}
    words: list[str] = []
    for v in k.get("variants") or []:
        for f in ("name", "top", "middle", "base", "desc"):
            words += re.split(r"[、，,/\s·]+", str(v.get(f) or ""))
    for r in k.get("ingredients") or []:
        words.append(str(r.get("name") or ""))
    for r in k.get("selling_points") or []:
        words.append(str(r.get("title") or ""))
    words += list(k.get("scenarios") or [])
    return [w for w in words if len(w) >= 1]


async def recommend(db: AsyncSession, user: User, *, knowledge: dict | None, industry: str = "",
                    limit: int = 12, scene_key: str = "") -> list[EcomElement]:
    """按商品资料（香调/成分/卖点）与行业推荐元素：命中关键词的在前，再补行业常用的水与光影类氛围元素。

    选的是节日场景时，节日包排最前（取 6 个），再接商品资料命中的元素。
    """
    from app.services.ecom_set import festivals

    await ensure_seeded(db)
    fest = festivals.of_scene(scene_key)
    if fest is not None:
        head = await get_many(db, user, festivals.pack_keys(fest)[:6])
        rest = await recommend(db, user, knowledge=knowledge, industry=industry, limit=limit)
        hit = [r for r in rest if r not in head and any(kw in " ".join(_knowledge_words(knowledge)) for kw in r.keywords or [])]
        return (head + hit)[:limit]
    rows, _ = await list_elements(db, user, industry=industry, page_size=300)
    text = " ".join(_knowledge_words(knowledge))
    scored: list[tuple[int, EcomElement]] = []
    for r in rows:
        if r.scope == "user":
            continue
        hits = sum(1 for kw in (r.keywords or []) if kw and kw in text)
        if hits:
            scored.append((hits, r))
    scored.sort(key=lambda x: -x[0])
    picked = [r for _, r in scored][: limit - 4]
    # 补 1～2 个光影 / 水感 / 材质氛围元素，保证画面层次
    for cat in ("light", "natural", "water", "fabric"):
        extra = next((r for r in rows if r.category == cat and r.scope == "system" and r not in picked), None)
        if extra is not None and len(picked) < limit:
            picked.append(extra)
    return picked


def prompt_line(elements: list[dict[str, Any]]) -> str:
    """本张图的元素 → 提示词片段；带实物照的元素提示按参考图画。"""
    if not elements:
        return ""
    names = "、".join(e["prompt"] or e["name"] for e in elements)
    text = (f"画面元素：{names}，只放这几样、每样一两件，元素小巧精致、比例真实，错落点缀在商品旁，不遮挡商品；"
            "画面保持大面积干净留白，道具不要堆满、不要铺满台面")
    refs = [e["name"] for e in elements if e.get("image_url")]
    if refs:
        text += f"；其中{'、'.join(refs)}请按后面附带的实物参考图画出，保持其外观"
    return text
