"""「我的商品」：商品资料（品类、尺寸、材质、卖点）+ 多个颜色款平铺图 + 可选面料实拍。"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_ecom import EcomProduct
from app.services.ecom_set import categories
from app.services.ecom_set.prompts import clip

MAX_COLORS = 12


def _save(user: User, data: bytes, filename: str) -> tuple[str, str]:
    """校验并保存图片，返回 (本地路径, 对外地址)。"""
    from app.services.ecom_set.jobs import public_url
    from app.services.ecom_set.service import save_product_image

    path = save_product_image(user.id, data, filename)
    return str(path), public_url(path)


def _normalize(category: str, size: str | None, material: str | None) -> tuple[str, str, str]:
    """规整品类 / 尺寸 / 材质。"""
    cat = category if categories.is_styled(category) else "scarf"
    return cat, categories.normalize_size(cat, size), categories.normalize_material(cat, material)


async def create_product(
    db: AsyncSession, user: User, *, name: str, category: str, size: str | None, material: str | None,
    notes: str, colors: list[tuple[str, bytes, str]], fabric: tuple[bytes, str] | None,
    reals: dict[int, tuple[bytes, str]] | None = None,
) -> EcomProduct:
    """新建商品：至少一个颜色款（颜色名 + 平铺图）。"""
    name = clip(name, 60)
    if not name:
        raise ValueError("请填写款名")
    if not colors:
        raise ValueError("请至少上传一张商品图（一个颜色款）")
    if len(colors) > MAX_COLORS:
        raise ValueError(f"颜色款最多 {MAX_COLORS} 个")
    cat, size, material = _normalize(category, size, material)
    saved = []
    for i, (cname, data, filename) in enumerate(colors):
        path, url = await asyncio.to_thread(_save, user, data, filename)
        item = {"name": clip(cname, 20) or f"颜色{i + 1}", "image_path": path, "image_url": url}
        if reals and i in reals:
            item["real_image_path"], item["real_image_url"] = await asyncio.to_thread(_save, user, *reals[i])
        saved.append(item)
    row = EcomProduct(user_id=user.id, name=name, category=cat, size=size, material=material,
                      notes=clip(notes, 500), colors=saved)
    if fabric is not None:
        row.fabric_image_path, row.fabric_image_url = await asyncio.to_thread(_save, user, *fabric)
    db.add(row)
    await db.commit()
    return row


async def get_product(db: AsyncSession, user: User, product_id: int) -> EcomProduct:
    """读取本人的一款商品。"""
    row = await db.get(EcomProduct, product_id)
    if row is None or row.user_id != user.id:
        raise LookupError("商品不存在")
    return row


async def update_product(db: AsyncSession, user: User, product_id: int, body: dict) -> EcomProduct:
    """改款名 / 品类 / 尺寸 / 材质 / 卖点 / 颜色名（不改图片）。"""
    row = await get_product(db, user, product_id)
    if "name" in body:
        name = clip(str(body.get("name") or ""), 60)
        if not name:
            raise ValueError("请填写款名")
        row.name = name
    cat = str(body.get("category") or row.category)
    row.category, row.size, row.material = _normalize(cat, body.get("size", row.size), body.get("material", row.material))
    if "notes" in body:
        row.notes = clip(str(body.get("notes") or ""), 500)
    names = body.get("color_names")
    if isinstance(names, list):
        colors = [dict(c) for c in row.colors or []]
        for i, cname in enumerate(names[: len(colors)]):
            colors[i]["name"] = clip(str(cname or ""), 20) or colors[i]["name"]
        row.colors = colors
    await db.commit()
    return row


async def add_color(db: AsyncSession, user: User, product_id: int, *, name: str, data: bytes, filename: str) -> EcomProduct:
    """追加一个颜色款。"""
    row = await get_product(db, user, product_id)
    colors = list(row.colors or [])
    if len(colors) >= MAX_COLORS:
        raise ValueError(f"颜色款最多 {MAX_COLORS} 个")
    path, url = await asyncio.to_thread(_save, user, data, filename)
    colors.append({"name": clip(name, 20) or f"颜色{len(colors) + 1}", "image_path": path, "image_url": url})
    row.colors = colors
    await db.commit()
    return row


async def set_color_real(db: AsyncSession, user: User, product_id: int, index: int,
                         upload: tuple[bytes, str] | None) -> EcomProduct:
    """给某个颜色款设置或清除实物图（真实拍摄的商品照片）。"""
    row = await get_product(db, user, product_id)
    colors = [dict(c) for c in row.colors or []]
    if not 0 <= index < len(colors):
        raise LookupError("颜色款不存在")
    if upload is None:
        colors[index].pop("real_image_path", None)
        colors[index].pop("real_image_url", None)
    else:
        colors[index]["real_image_path"], colors[index]["real_image_url"] = await asyncio.to_thread(_save, user, *upload)
    row.colors = colors
    await db.commit()
    return row


async def remove_color(db: AsyncSession, user: User, product_id: int, index: int) -> EcomProduct:
    """删除一个颜色款（至少保留一个）。"""
    row = await get_product(db, user, product_id)
    colors = list(row.colors or [])
    if not 0 <= index < len(colors):
        raise LookupError("颜色款不存在")
    if len(colors) == 1:
        raise ValueError("至少保留一个颜色款")
    colors.pop(index)
    row.colors = colors
    await db.commit()
    return row


async def set_fabric(db: AsyncSession, user: User, product_id: int, upload: tuple[bytes, str] | None) -> EcomProduct:
    """设置或清除面料实拍图。"""
    row = await get_product(db, user, product_id)
    if upload is None:
        row.fabric_image_path, row.fabric_image_url = "", ""
    else:
        row.fabric_image_path, row.fabric_image_url = await asyncio.to_thread(_save, user, *upload)
    await db.commit()
    return row


async def delete_product(db: AsyncSession, user: User, product_id: int) -> None:
    """删除商品资料（已生成的套图不受影响）。"""
    row = await get_product(db, user, product_id)
    await db.delete(row)
    await db.commit()


async def list_products(db: AsyncSession, user: User, *, page: int, page_size: int) -> tuple[list[EcomProduct], int]:
    """本人商品列表（服务端分页，新的在前）。"""
    page, page_size = max(1, page), min(max(1, page_size), 50)
    base = select(EcomProduct).where(EcomProduct.user_id == user.id)
    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = (await db.execute(base.order_by(EcomProduct.updated_at.desc(), EcomProduct.id.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return list(rows), total


def color_of(row: EcomProduct, index: int) -> dict:
    """取第 index 个颜色款。"""
    colors = list(row.colors or [])
    if not 0 <= index < len(colors):
        raise ValueError("颜色款不存在")
    return colors[index]


def to_out(row: EcomProduct) -> dict:
    """序列化给前端（不含本地路径）。"""
    return {
        "id": row.id, "name": row.name, "category": row.category, "size": row.size, "material": row.material,
        "material_label": categories.material_label(row.category, row.material) if categories.is_styled(row.category) else "",
        "notes": row.notes or "",
        "colors": [{"name": c.get("name") or "", "image_url": c.get("image_url") or "",
                    "real_image_url": c.get("real_image_url") or ""} for c in row.colors or []],
        "fabric_image_url": row.fabric_image_url or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }
