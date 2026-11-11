"""「我的商品」：商品资料（品类、尺寸、材质、卖点）+ 多个颜色款平铺图 + 可选面料实拍。"""

from __future__ import annotations

import asyncio
from pathlib import Path

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


def _save_color(user: User, data: bytes, filename: str) -> dict:
    """保存一张颜色款商品图，并记下它是不是透明底（不是透明底的可以一键抠图）。"""
    from app.services.ecom_set.composite import has_alpha

    path, url = _save(user, data, filename)
    try:
        transparent = has_alpha(Path(path))
    except Exception:  # noqa: BLE001
        transparent = False
    return {"image_path": path, "image_url": url, "transparent": transparent}


def _dims(category: str, value: dict | None) -> dict | None:
    """规整实际尺寸；丝巾、服饰不存，空尺寸存 None。"""
    from app.services.ecom_set import dims as dims_mod

    if category in dims_mod.NO_DIMS_CATEGORIES:
        return None
    return dims_mod.normalize(value or {}) or None


def _normalize(category: str, size: str | None, material: str | None) -> tuple[str, str, str]:
    """规整品类 / 尺寸 / 材质。"""
    cat = category if categories.is_styled(category) else "scarf"
    return cat, categories.normalize_size(cat, size), categories.normalize_material(cat, material)


async def create_product(
    db: AsyncSession, user: User, *, name: str, category: str, size: str | None, material: str | None,
    notes: str, colors: list[tuple[str, bytes, str]], fabric: tuple[bytes, str] | None,
    reals: dict[int, tuple[bytes, str]] | None = None, dims: dict | None = None,
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
        item = {"name": clip(cname, 20) or f"颜色{i + 1}", **await asyncio.to_thread(_save_color, user, data, filename)}
        if reals and i in reals:
            item["real_image_path"], item["real_image_url"] = await asyncio.to_thread(_save, user, *reals[i])
        saved.append(item)
    row = EcomProduct(user_id=user.id, name=name, category=cat, size=size, material=material,
                      notes=clip(notes, 500), colors=saved, dims=_dims(cat, dims))
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
    row.dims = _dims(row.category, body.get("dims", row.dims))
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
    colors.append({"name": clip(name, 20) or f"颜色{len(colors) + 1}", **await asyncio.to_thread(_save_color, user, data, filename)})
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


def color_photo_path(row: EcomProduct, index: int) -> Path:
    """颜色款用来抠图的原图（抠过图的取保留的原图）。"""
    colors = list(row.colors or [])
    if not 0 <= index < len(colors):
        raise LookupError("颜色款不存在")
    path = Path(colors[index].get("photo_image_path") or colors[index].get("image_path") or "")
    if not path.is_file():
        raise LookupError("商品图不存在，请重新上传")
    return path


async def set_color_cutout(db: AsyncSession, user: User, product_id: int, index: int, data: bytes, filename: str) -> EcomProduct:
    """保存浏览器抠好的透明底 PNG 作为颜色款商品图；原图留作 photo_image_*，可随时切回。"""
    from app.services.ecom_set.composite import has_alpha

    row = await get_product(db, user, product_id)
    colors = [dict(c) for c in row.colors or []]
    if not 0 <= index < len(colors):
        raise LookupError("颜色款不存在")
    if not str(filename or "").lower().endswith(".png"):
        raise ValueError("抠图结果需要是 PNG")
    path, url = await asyncio.to_thread(_save, user, data, filename)
    if not await asyncio.to_thread(has_alpha, Path(path)):
        Path(path).unlink(missing_ok=True)
        raise ValueError("抠图结果不是透明底，请重试")
    c = colors[index]
    c.setdefault("photo_image_path", c.get("image_path"))
    c.setdefault("photo_image_url", c.get("image_url"))
    c["image_path"], c["image_url"], c["transparent"] = path, url, True
    colors[index] = c
    row.colors = colors
    await db.commit()
    return row


async def restore_color_photo(db: AsyncSession, user: User, product_id: int, index: int) -> EcomProduct:
    """不用抠图，切回上传的原图。"""
    row = await get_product(db, user, product_id)
    colors = [dict(c) for c in row.colors or []]
    if not 0 <= index < len(colors):
        raise LookupError("颜色款不存在")
    c = colors[index]
    if not c.get("photo_image_path"):
        raise ValueError("这张没有抠过图")
    c["image_path"], c["image_url"] = c.pop("photo_image_path"), c.pop("photo_image_url", "")
    c["transparent"] = False
    colors[index] = c
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


# ---------- 参考详情与商品资料 ----------
async def add_references(db: AsyncSession, user: User, product_id: int, *, kind: str,
                         files: list[tuple[bytes, str]]) -> EcomProduct:
    """上传参考详情图（kind：own 品牌自己的 | competitor 竞品）。"""
    from app.services.ecom_set.knowledge import MAX_REFERENCES

    row = await get_product(db, user, product_id)
    refs = [dict(r) for r in row.ref_images or []]
    if not files:
        raise ValueError("请选择参考详情图")
    if len(refs) + len(files) > MAX_REFERENCES:
        raise ValueError(f"参考详情图最多 {MAX_REFERENCES} 张")
    kind = "competitor" if kind == "competitor" else "own"
    for data, filename in files:
        path, url = await asyncio.to_thread(_save, user, data, filename)
        refs.append({"path": path, "url": url, "kind": kind})
    row.ref_images = refs
    await db.commit()
    return row


async def remove_reference(db: AsyncSession, user: User, product_id: int, index: int) -> EcomProduct:
    """删除一张参考详情图（已整理的资料保留）。"""
    row = await get_product(db, user, product_id)
    refs = [dict(r) for r in row.ref_images or []]
    if not 0 <= index < len(refs):
        raise LookupError("参考图不存在")
    refs.pop(index)
    row.ref_images = refs
    await db.commit()
    return row


async def set_knowledge(db: AsyncSession, user: User, product_id: int, body: dict) -> EcomProduct:
    """保存用户修改后的商品资料（重新做极限词提示）。"""
    from app.services.ecom_set import knowledge as kn

    row = await get_product(db, user, product_id)
    data = kn.normalize(body)
    row.knowledge = None if kn.is_empty(data) else kn.stamp(data, kn.compliance_warnings(data))
    await db.commit()
    return row


async def extract_knowledge(db: AsyncSession, user: User, product_id: int) -> EcomProduct:
    """AI 读参考详情整理商品资料（按视觉调用批次计费）；已有资料里用户填的内容会被新结果补充而不是清空。"""
    from pathlib import Path

    from app.config import get_settings
    from app.services.billing import run_billed_ephemeral
    from app.services.ecom_set import knowledge as kn

    row = await get_product(db, user, product_id)
    refs = [r for r in row.ref_images or [] if Path(str(r.get("path") or "")).exists()]
    if not refs:
        raise ValueError("请先上传参考详情图")
    own = [Path(r["path"]) for r in refs if r.get("kind") != "competitor"]
    comp = [Path(r["path"]) for r in refs if r.get("kind") == "competitor"]
    settings = get_settings()
    batches = 1 if settings.ark_mock else len(await asyncio.to_thread(kn.plan_batches, own, comp))

    async def _exec() -> tuple[dict, list[str]]:
        k, warns, calls = await kn.extract(own=own, competitor=comp, name=row.name, mock=settings.ark_mock)
        await _record_knowledge_charge(db, user.id, calls)
        return k, warns

    _, (k, warns) = await run_billed_ephemeral(db, user, domain="studio", task_type="ecom_knowledge",
                                               executor=_exec, payload={"batches": batches, "product_id": row.id},
                                               commit=False)
    old = kn.normalize(row.knowledge or {})
    merged = kn.merge([k, old]) if not kn.is_empty(old) else k
    row.knowledge = kn.stamp(merged, warns)
    await db.commit()
    return row


async def _record_knowledge_charge(db: AsyncSession, user_id: int, calls: int) -> None:
    """在计费范围内按实际视觉调用次数写用量行（单价同规划）。"""
    from datetime import UTC, datetime

    from app.config import get_settings
    from app.models import UsageEvent
    from app.services.billing.context import get_current_task_run_id

    tid = get_current_task_run_id()
    if tid is None or calls <= 0:
        return
    db.add(UsageEvent(
        user_id=user_id, task_run_id=tid, domain="studio", capability="llm", provider="ark",
        billing_key="ecom_knowledge", model="ecom_knowledge", total_tokens=0, cost_fen=0,
        charge_fen=int(get_settings().ecom_set_plan_fen) * calls, estimated=False, settled=False,
        created_at=datetime.now(UTC),
    ))
    await db.flush()


def color_of(row: EcomProduct, index: int) -> dict:
    """取第 index 个颜色款。"""
    colors = list(row.colors or [])
    if not 0 <= index < len(colors):
        raise ValueError("颜色款不存在")
    return colors[index]


def knowledge_out(data: dict | None) -> dict | None:
    """商品资料序列化（空资料返回 None）。"""
    from app.services.ecom_set import knowledge as kn

    if not data or kn.is_empty(data):
        return None
    return {**kn.normalize(data), "warnings": list(data.get("warnings") or []), "updated_at": data.get("updated_at") or ""}


def to_out(row: EcomProduct) -> dict:
    """序列化给前端（不含本地路径）。"""
    return {
        "id": row.id, "name": row.name, "category": row.category, "size": row.size, "material": row.material,
        "dims": row.dims or {},
        "material_label": categories.material_label(row.category, row.material) if categories.is_styled(row.category) else "",
        "notes": row.notes or "",
        "colors": [{"name": c.get("name") or "", "image_url": c.get("image_url") or "",
                    "real_image_url": c.get("real_image_url") or "",
                    "photo_image_url": c.get("photo_image_url") or "",
                    # None = 老数据没记录（前端照样给「抠白底」按钮，后端会拦已透明的）
                    "transparent": c.get("transparent")} for c in row.colors or []],
        "fabric_image_url": row.fabric_image_url or "",
        "references": [{"url": r.get("url") or "", "kind": r.get("kind") or "own"} for r in row.ref_images or []],
        "knowledge": knowledge_out(row.knowledge),
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }
