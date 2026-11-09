"""一图出套图业务层：建套图（规划）、改方案、批量入队、单张重做/改文案、打包、列表。"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User
from app.models_ecom import EcomSet, EcomSetItem
from app.models_tasks import TaskRun
from app.schemas_tasks import TaskCreateRequest
from app.services import storage
from app.services.billing import run_billed_ephemeral
from app.services.billing.estimates import estimate_task_fen
from app.services.billing.settlement import billing_active, ensure_balance_for_task_batch
from app.services.ecom_set.jobs import (
    public_url,
    reconcile_stuck_items,
    render_flat_item,
    refresh_set_status,
    render_final,
    set_dir,
)
from app.services.ecom_set.planner import plan_set
from app.services.ecom_set import categories
from app.services.ecom_set import scenes as scenes_mod
from app.services.ecom_set import typo as typo_mod
from app.services.ecom_set.categories import is_styled
from app.services.ecom_set.styled import build_long_image, create_styled_plan, recolor_set, styled_out
from app.services.ecom_set.presets import (
    CATEGORIES,
    FREE_REFS,
    SLOTS,
    normalize_category,
    slots_for,
    COUNTS,
    PLATFORMS,
    STYLES,
    gen_ratio,
    normalize_count,
    normalize_platform,
    normalize_style,
    slot_label,
)
from app.services.compliance.ad_words import Hit, summarize
from app.services.ecom_set.prompts import clip, normalize_copy, sanitize_scene, title_limit

logger = logging.getLogger(__name__)

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD = 10 * 1024 * 1024


# 校验并保存商品图，返回本地路径
def save_product_image(user_id: int, data: bytes, filename: str) -> Path:
    from PIL import Image

    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("商品图仅支持 png / jpg / webp")
    if not data:
        raise ValueError("请上传商品图")
    if len(data) > MAX_UPLOAD:
        raise ValueError("商品图不能超过 10MB")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("图片无法识别，请换一张") from exc
    dest = storage.project_dir(0) / "ecom" / f"u{user_id}" / "uploads" / f"{uuid.uuid4().hex[:12]}{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


# 出图模型：seedream 默认（主渠道）| gpt-image-2（后台「生图」渠道：OpenAI 官方或 Promptoll）
IMAGE_ENGINES = ("seedream", "gpt-image-2")


def normalize_engine(value: str | None) -> str:
    """规整出图模型，未知回落 seedream。"""
    v = (value or "").strip()
    return v if v in IMAGE_ENGINES else "seedream"


def engine_of(ecom: EcomSet) -> str:
    """套图选的出图模型（旧套图为 seedream）。"""
    plan = ecom.plan or {}
    info = plan.get("styled") if isinstance(plan.get("styled"), dict) else plan
    return normalize_engine((info or {}).get("image_engine"))


def item_engine(ecom: EcomSet, item: EcomSetItem) -> str:
    """单张实际用的出图模型：质地特写不带商品参考图，GPT 会画出无品牌的包装，固定用 Seedream。"""
    engine = engine_of(ecom)
    slot = SLOTS.get(item.slot)
    if engine == "gpt-image-2" and slot is not None and slot.ref == "texture":
        return "seedream"
    return engine


def engine_options() -> list[dict]:
    """前台可选的出图模型：GPT Image 2 只在后台开通了生图渠道时出现。"""
    from app.services.openai_image import openai_image_configured, provider_label

    out = [{"id": "seedream", "name": "Seedream 5.0 Pro", "hint": "默认，出图稳定、性价比高"}]
    if openai_image_configured():
        out.append({"id": "gpt-image-2", "name": f"GPT Image 2（{provider_label()}）",
                    "hint": "画面质感与排版感更强，单价更高"})
    return out


# 单张生图预估价（分）
async def image_unit_fen(db: AsyncSession, engine: str = "seedream") -> int:
    from app.services.media_models.planning import prepare_task_media

    probe = TaskRun(domain="studio", task_type="ecom_set_image", payload=image_task_payload("3:4", engine))
    try:
        await prepare_task_media(db, probe)
    except ValueError as exc:
        # 模型渠道未配置时只是报不出价，不能让选项和历史列表整页 500；真正提交生成时仍会报错。
        logger.warning("电商图估价跳过：%s", exc)
        return 0
    return int(await estimate_task_fen(db, probe))


# 套图单张生图任务的计价参数（与图片生成页同一张价目表：2K）
def image_task_payload(ratio: str, engine: str = "seedream") -> dict:
    body = {"aspect_ratio": ratio or "3:4", "resolution": "2K"}
    if normalize_engine(engine) == "gpt-image-2":
        body["model_id"] = "gpt-image-2"
    return body


# 前端选项：平台/风格/张数/价格
async def get_options(db: AsyncSession) -> dict:
    s = get_settings()
    return {
        "platforms": [{"id": k, "name": v[0]} for k, v in PLATFORMS.items()],
        "styles": [{"id": k, "name": v[0]} for k, v in STYLES.items()],
        "counts": list(COUNTS),
        "categories": [{"id": k, "name": v} for k, v in CATEGORIES.items()],
        "styled": categories.options(),
        "frames": [{"id": k, "name": v[0]} for k, v in categories.FRAMES.items()],
        "industries": scenes_mod.industries_out(),
        "category_industry": dict(scenes_mod.CATEGORY_INDUSTRY),
        "type_styles": typo_mod.options(),
        "image_engines": [{**e, "image_fen": await image_unit_fen(db, e["id"])} for e in engine_options()],
        "scarf_sizes": next(c["sizes"] for c in categories.options() if c["id"] == "scarf"),
        "scarf_counts": next(c["counts"] for c in categories.options() if c["id"] == "scarf"),
        "scarf_materials": next(c["materials"] for c in categories.options() if c["id"] == "scarf"),
        "plan_fen": int(s.ecom_set_plan_fen),
        "image_fen": await image_unit_fen(db),
        "billing_enabled": billing_active(),
    }


# 在计费范围内写一条固定价的规划用量行
async def _record_plan_charge(db: AsyncSession, user_id: int) -> None:
    from datetime import UTC, datetime

    from app.models import UsageEvent
    from app.services.billing.context import get_current_task_run_id

    tid = get_current_task_run_id()
    if tid is None:
        return
    db.add(UsageEvent(
        user_id=user_id, task_run_id=tid, domain="studio", capability="llm", provider="ark",
        billing_key="ecom_set_plan", model="ecom_set_plan", total_tokens=0,
        cost_fen=0, charge_fen=int(get_settings().ecom_set_plan_fen), estimated=False,
        settled=False, created_at=datetime.now(UTC),
    ))
    await db.flush()


async def create_plan(
    db: AsyncSession, user: User, *, data: bytes | None, filename: str,
    name: str, platform: str, style: str, count: int | str | None, notes: str,
    category: str = "general", scarf_size: str | None = None, model_asset_id: int | None = None,
    scarf_material: str | None = None, frame: str | None = None, slots_picked: list[str] | None = None,
    product_id: int | None = None, color_index: int = 0, fabric: tuple[bytes, str] | None = None,
    scene_key: str | None = None, image_engine: str | None = None,
) -> EcomSet:
    """上传商品图（或从商品库选）→ 计费规划 → 落库套图与各张方案（status=planned）。

    丝巾 / 服饰 / 首饰 / 包袋为「模特上身」套图：模特槽位以模特库里的模特为原型，材质、尺寸、画幅与槽位可选。
    """
    category = normalize_category(category)
    if is_styled(category) or product_id:
        set_id = await create_styled_plan(
            db, user, category=category, name=name, notes=notes, count=count, size=scarf_size,
            material=scarf_material, model_asset_id=model_asset_id, frame=frame, slots_picked=slots_picked,
            product_id=product_id, color_index=color_index, data=data, filename=filename, fabric=fabric,
            scene_key=scene_key, image_engine=image_engine,
        )
        return await get_set(db, user, set_id)
    name = clip(name, 60)
    if not name:
        raise ValueError("请填写品名")
    if not data:
        raise ValueError("请上传商品图")
    platform, style = normalize_platform(platform), normalize_style(style)
    count = normalize_count(count)
    path = save_product_image(user.id, data, filename)
    slots = slots_for(category, count)

    async def _exec() -> dict:
        result = await plan_set(
            image_path=path, name=name, notes=clip(notes, 500), platform=platform, style=style, slots=slots
        )
        await _record_plan_charge(db, user.id)
        return result

    billing_task, plan = await run_billed_ephemeral(
        db, user, domain="studio", task_type="ecom_set_plan", executor=_exec,
        payload={"count": count, "platform": platform}, commit=False,
    )
    ecom = EcomSet(
        user_id=user.id, status="planned", name=name, platform=platform, style=style, count=count,
        notes=clip(notes, 500), product_image_path=str(path),
        product_image_url=await asyncio.to_thread(public_url, path),
        plan={"product": plan["product"], "warnings": plan["warnings"], "vision": plan["vision"]},
        plan_task_id=billing_task.id,
    )
    db.add(ecom)
    await db.flush()
    for idx, (slot, it) in enumerate(zip(slots, plan["items"], strict=True)):
        db.add(EcomSetItem(
            set_id=ecom.id, idx=idx, slot=slot.key, layout=slot.layout,
            ratio=gen_ratio(platform, slot.key), blank=slot.blank,
            scene=it["scene"], status="planned", copy_json=it["copy"],
        ))
    await db.commit()
    return await get_set(db, user, ecom.id)


# 是否为不调模型的槽位（尺寸图、平铺图）
def _is_flat(item: EcomSetItem) -> bool:
    slot = SLOTS.get(item.slot)
    return bool(slot and slot.ref in FREE_REFS)


# 不调模型的槽位直接排版成图（免费、同步）
async def _render_flat_items(ecom: EcomSet, items: list[EcomSetItem]) -> None:
    for it in items:
        out = await asyncio.to_thread(render_flat_item, ecom, it)
        url = await asyncio.to_thread(public_url, out)
        it.base_path, it.base_url, it.final_url = str(out), url, url
        it.status, it.error, it.task_run_id = "succeeded", None, None


# 读取当前用户的一套图（含各张）
async def get_set(db: AsyncSession, user: User, set_id: int) -> EcomSet:
    row = (
        await db.execute(
            select(EcomSet).where(EcomSet.id == set_id, EcomSet.user_id == user.id).execution_options(
                populate_existing=True
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise LookupError("套图不存在")
    if row.status == "generating" and await reconcile_stuck_items(db, row):
        return await get_set(db, user, set_id)
    return row


# 把用户手改命中的极限词提示写进套图 warnings（覆盖同类旧提示）
def _set_edit_warnings(ecom: EcomSet, hits: list[Hit]) -> None:
    plan = dict(ecom.plan or {})
    base = [w for w in plan.get("warnings") or [] if not w.startswith(("已自动替换", "以下用语"))]
    plan["warnings"] = base + summarize(hits)
    ecom.plan = plan


# 取套图中的某一张
def _item(ecom: EcomSet, idx: int) -> EcomSetItem:
    for it in ecom.items:
        if it.idx == idx:
            return it
    raise LookupError("图片不存在")


async def update_plan(db: AsyncSession, user: User, set_id: int, items: list[dict]) -> EcomSet:
    """生成前修改方案（画面与文案），免费。"""
    ecom = await get_set(db, user, set_id)
    if ecom.status != "planned":
        raise ValueError("已开始生成，不能再修改方案")
    hits: list[Hit] = []
    for body in items:
        it = _item(ecom, int(body.get("idx", -1)))
        if "scene" in body:
            scene, sh = sanitize_scene(str(body.get("scene") or ""))
            it.scene = clip(scene, 300)
            hits += sh
        copy, ch = normalize_copy(_keep_en(body, it), layout=it.layout, title_max=title_limit(it.slot))
        it.copy_json = copy
        hits += ch
    _set_edit_warnings(ecom, hits)
    await db.commit()
    return await get_set(db, user, set_id)


# 为若干张创建生图任务（同一事务，余额不足整体回滚）
async def _enqueue_items(db: AsyncSession, user: User, ecom: EcomSet, items: list[EcomSetItem]) -> None:
    from app.services.tasks.service import create_task

    probe = TaskRun(domain="studio", task_type="ecom_set_image", requested_by=user.id,
                    payload=image_task_payload(items[0].ratio if items else "3:4", item_engine(ecom, items[0]) if items else "seedream"))
    await ensure_balance_for_task_batch(db, user, probe, len(items))
    for it in items:
        task = await create_task(
            db, user,
            TaskCreateRequest(
                domain="studio", task_type="ecom_set_image",
                payload={"set_id": ecom.id, "idx": it.idx, "slot": it.slot, "ratio": it.ratio,
                         **image_task_payload(it.ratio, item_engine(ecom, it))},
                dedupe_key=f"ecomset:{ecom.id}:{it.idx}:{uuid.uuid4().hex[:6]}",
            ),
            commit=False,
        )
        it.task_run_id = task.id
        it.status = "queued"
        it.error = None


async def start_generation(db: AsyncSession, user: User, set_id: int) -> EcomSet:
    """确认方案后批量入队所有未完成的图。"""
    ecom = await get_set(db, user, set_id)
    todo = [it for it in ecom.items if it.status in {"planned", "failed"}]
    if not todo:
        raise ValueError("没有需要生成的图片")
    ai_items = [it for it in todo if not _is_flat(it)]
    if ai_items:
        await _enqueue_items(db, user, ecom, ai_items)
    await _render_flat_items(ecom, [it for it in todo if _is_flat(it)])
    ecom.status = "generating" if ai_items else "done"
    await db.commit()
    return await get_set(db, user, set_id)


async def regenerate_item(db: AsyncSession, user: User, set_id: int, idx: int, scene: str | None) -> EcomSet:
    """单张重新生成底图（收一张生图费），可顺带改画面描述。"""
    ecom = await get_set(db, user, set_id)
    it = _item(ecom, idx)
    if it.status in {"queued", "running"}:
        raise ValueError("这张图正在生成中")
    if scene is not None and scene.strip():
        clean, hits = sanitize_scene(scene)
        it.scene = clip(clean, 300)
        _set_edit_warnings(ecom, hits)
    if _is_flat(it):
        if SLOTS[it.slot].ref == "info":
            await _refresh_knowledge(db, user, ecom)
        await _render_flat_items(ecom, [it])
        await refresh_set_status(db, ecom.id)
    else:
        await _enqueue_items(db, user, ecom, [it])
        ecom.status = "generating"
    await db.commit()
    return await get_set(db, user, set_id)


async def _refresh_knowledge(db: AsyncSession, user: User, ecom: EcomSet) -> None:
    """信息屏重新生成前，用商品库里最新的资料与颜色款刷新套图快照（用户改了资料后点重新生成即生效）。"""
    from app.services.ecom_set import products
    from app.services.ecom_set.styled import _knowledge_of

    styled = dict((ecom.plan or {}).get("styled") or {})
    if not styled.get("product_id"):
        return
    try:
        row = await products.get_product(db, user, int(styled["product_id"]))
    except LookupError:
        return
    know, variants = _knowledge_of(row)
    styled.update(knowledge=know, variants=variants)
    ecom.plan = {**(ecom.plan or {}), "styled": styled}


async def update_copy(db: AsyncSession, user: User, set_id: int, idx: int, body: dict) -> EcomSet:
    """改文案并基于已有底图重新排版（不调模型、不收费）。"""
    ecom = await get_set(db, user, set_id)
    it = _item(ecom, idx)
    if not it.base_path or not Path(it.base_path).exists():
        raise ValueError("这张图还没有底图，请先生成")
    copy, hits = normalize_copy(_keep_en(body, it), layout=it.layout, title_max=title_limit(it.slot))
    it.copy_json = copy
    _set_edit_warnings(ecom, hits)
    _, it.final_url = await render_final(ecom, it, Path(it.base_path))
    await refresh_set_status(db, ecom.id)
    await db.commit()
    return await get_set(db, user, set_id)


def _keep_en(body: dict, it: EcomSetItem) -> dict:
    """请求里没带英文小标（None）时沿用原值。"""
    if body.get("en") is None:
        return {**body, "en": (it.copy_json or {}).get("en") or ""}
    return body


async def set_type_style(db: AsyncSession, user: User, set_id: int, type_style: str) -> EcomSet:
    """切换整套图的字体风格：已出图的全部重新排字（不调模型、不收费）。"""
    from app.services.ecom_set import typo

    if type_style not in typo.TYPE_STYLES:
        raise ValueError("不支持的字体风格")
    ecom = await get_set(db, user, set_id)
    plan = dict(ecom.plan or {})
    if isinstance(plan.get("styled"), dict):
        plan["styled"] = {**plan["styled"], "type_style": type_style}
    else:
        plan["type_style"] = type_style
    ecom.plan = plan
    for it in ecom.items:
        if it.status == "succeeded" and it.base_path and Path(it.base_path).exists():
            _, it.final_url = await render_final(ecom, it, Path(it.base_path))
    await db.commit()
    return await get_set(db, user, set_id)


async def build_zip(db: AsyncSession, user: User, set_id: int) -> tuple[bytes, str]:
    """把已完成的成图打成 zip，返回 (字节, 文件名)。"""
    ecom = await get_set(db, user, set_id)
    done = [it for it in ecom.items if it.final_url]
    if not done:
        raise ValueError("还没有生成好的图片")
    buf = io.BytesIO()
    workdir = set_dir(ecom.user_id, ecom.id)
    locals_: list[Path] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in done:
            local = storage.local_path_from_url(it.final_url or "")
            if local is None or not local.exists():
                local = await storage.ensure_local_media(it.final_url or "", workdir / f"dl_{it.idx:02d}.jpg")
            locals_.append(Path(local))
            zf.write(local, f"{it.idx + 1:02d}-{slot_label(it.slot)}.jpg")
        if len(locals_) >= 2:
            zf.writestr("00-详情长图.jpg", await asyncio.to_thread(build_long_image, locals_))
    safe = "".join(ch for ch in ecom.name if ch.isalnum())[:20] or "ecomset"
    return buf.getvalue(), f"{safe}-{ecom.id}.zip"


async def long_image(db: AsyncSession, user: User, set_id: int) -> tuple[bytes, str]:
    """已完成成图按顺序拼成的详情长图，返回 (JPEG 字节, 文件名)。"""
    ecom = await get_set(db, user, set_id)
    paths = []
    for it in ecom.items:
        if not it.final_url:
            continue
        local = storage.local_path_from_url(it.final_url)
        if local is None or not local.exists():
            local = await storage.ensure_local_media(it.final_url, set_dir(ecom.user_id, ecom.id) / f"dl_{it.idx:02d}.jpg")
        paths.append(Path(local))
    data = await asyncio.to_thread(build_long_image, paths)
    safe = "".join(ch for ch in ecom.name if ch.isalnum())[:20] or "ecomset"
    return data, f"{safe}-{ecom.id}-详情长图.jpg"


async def recolor(db: AsyncSession, user: User, set_id: int, color_index: int) -> EcomSet:
    """同款换色：新建一套沿用原方案的套图（待生成）。"""
    return await get_set(db, user, await recolor_set(db, user, set_id, color_index))


async def list_sets(db: AsyncSession, user: User, *, page: int, page_size: int) -> tuple[list[EcomSet], int]:
    """当前用户的套图列表（服务端分页，按创建倒序）。"""
    page, page_size = max(1, page), min(max(1, page_size), 50)
    total = int(
        (await db.execute(select(func.count()).select_from(EcomSet).where(EcomSet.user_id == user.id))).scalar_one()
    )
    rows = (
        await db.execute(
            select(EcomSet).where(EcomSet.user_id == user.id)
            .order_by(EcomSet.created_at.desc(), EcomSet.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return list(rows), total


# 序列化一套图给前端
def to_out(ecom: EcomSet, *, image_fen: int) -> dict:
    plan = ecom.plan or {}
    styled = styled_out(ecom)
    items = []
    for it in ecom.items:
        copy = it.copy_json or {}
        items.append({
            "idx": it.idx, "slot": it.slot, "slot_label": slot_label(it.slot), "layout": it.layout,
            "ratio": it.ratio, "scene": it.scene or "", "title": copy.get("title") or "",
            "subtitle": copy.get("subtitle") or "", "bullets": list(copy.get("bullets") or []),
            "en": copy.get("en") or "",
            "base_url": it.base_url, "final_url": it.final_url, "status": it.status, "error": it.error,
            "billable": not _is_flat(it),
        })
    return {
        "id": ecom.id, "status": ecom.status, "name": ecom.name, "platform": ecom.platform,
        "style": ecom.style, "count": ecom.count, "product_image_url": ecom.product_image_url,
        "product": plan.get("product") or {"name": ecom.name, "category": "", "colors": [], "selling_points": []},
        "warnings": list(plan.get("warnings") or []), "image_fen": image_fen,
        "total_fen": image_fen * sum(1 for it in items if it["billable"]), "items": items,
        "category": styled["category"] if styled else "general",
        "scarf": styled, "styled": styled,
        "created_at": ecom.created_at.isoformat() if ecom.created_at else "",
        "project_id": ecom.project_id,
    }
