"""每张图错开镜头与元素：规划模型先选，缺的或重复的按规则补齐，保证整套构图与道具不雷同。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_ecom import EcomSet, EcomSetItem
from app.services.ecom_set import shots as shots_mod
from app.services.ecom_set.presets import Slot

# 需要分配镜头/元素的槽位类型
SHOT_REFS = {"composite"}
ELEMENT_REFS = {"composite", "texture", "hand", "product"}
# 各类槽位放几个元素
# 道具宁少勿满：原料静物 2 个、手持与陈列 1 个，其余默认 2 个
ELEMENT_COUNT = {"goods_ingredient_still": 2, "goods_texture": 1, "goods_hand": 1, "goods_lineup": 1}


def pool_from_scene(scene: dict | None) -> list[dict]:
    """没选元素时，把场景卡道具当作元素池（每张仍会错开挑选）。"""
    props = ((scene or {}).get("spec") or {}).get("props") or []
    return [{"key": f"scene_{i}", "name": p, "category": "natural", "prompt": p, "image_url": "", "image_path": ""}
            for i, p in enumerate(props) if p]


def hint_text(slots: list[Slot], pool: list[dict]) -> str:
    """给规划模型的「镜头与元素」说明。"""
    if not pool and not any(s.ref in SHOT_REFS for s in slots):
        return ""
    lines = ["镜头与元素（每张合成图按下面选，整套尽量不重复）："]
    for s in slots:
        if s.ref in SHOT_REFS and s.key != "goods_lineup":
            opts = "、".join(f"{k}={shots_mod.SHOTS[k].name}" for k in shots_mod.shot_choices(s.key))
            lines.append(f"- {s.key} 可选镜头：{opts}")
    if pool:
        lines.append("元素池：" + "、".join(e["name"] for e in pool))
    return "\n".join(lines)


def _match(names: Any, pool: list[dict]) -> list[str]:
    """把规划模型写的元素名对应到元素池 key（包含匹配）。"""
    out: list[str] = []
    for n in names if isinstance(names, list) else []:
        n = str(n or "").strip()
        if not n:
            continue
        hit = next((e["key"] for e in pool if e["name"] == n), None) or \
            next((e["key"] for e in pool if n in e["name"] or e["name"] in n), None)
        if hit and hit not in out:
            out.append(hit)
    return out


def assign(slots: list[Slot], planned: list[dict], pool: list[dict]) -> dict[str, dict]:
    """返回 {idx: {"shot": key, "elements": [key...]}}；只给需要的槽位分配。"""
    used_shots: set[str] = set()
    used_combos: set[tuple[str, ...]] = set()
    cursor = 0
    out: dict[str, dict] = {}
    for i, slot in enumerate(slots):
        src = planned[i] if i < len(planned) else {}
        entry: dict[str, Any] = {}
        if slot.ref in SHOT_REFS and slot.key != "goods_lineup":
            choices = shots_mod.shot_choices(slot.key)
            want = str(src.get("shot") or "")
            if want in choices and want not in used_shots:
                shot = want
            else:
                shot = next((k for k in choices if k not in used_shots), choices[0])
            used_shots.add(shot)
            entry["shot"] = shot
        if slot.ref in ELEMENT_REFS and pool:
            # 元素池小时每张少拿一个，组合才会轮换（3 个元素 → 每张 2 个，三种组合）
            default_n = 2 if len(pool) >= 3 else 1
            n = min(ELEMENT_COUNT.get(slot.key, default_n), max(1, len(pool) - 1) if len(pool) > 1 else 1) \
                if slot.key != "goods_ingredient_still" else min(ELEMENT_COUNT[slot.key], len(pool))
            picked = _match(src.get("elements"), pool)[:n]
            if slot.key == "goods_ingredient_still":
                # 原料静物：原料、植物类优先，从头挑
                candidates = [e for e in pool if e["category"] in {"produce", "plant"}] + \
                             [e for e in pool if e["category"] not in {"produce", "plant"}]
            else:
                # 其它图：从游标处轮转，让每张拿到不同的组合
                candidates = pool[cursor % len(pool):] + pool[: cursor % len(pool)]
                cursor += max(1, n - 1)
            for cand in (e["key"] for e in candidates):
                if len(picked) >= min(n, len(pool)) and tuple(sorted(picked)) not in used_combos:
                    break
                if cand in picked:
                    continue
                if len(picked) >= n:
                    picked.pop(0)
                picked.append(cand)
            used_combos.add(tuple(sorted(picked)))
            entry["elements"] = picked
        if entry:
            out[str(i)] = entry
    return out


def elements_of(pool: list[dict], keys: list[str]) -> list[dict]:
    """按 key 取元素快照。"""
    by = {e["key"]: e for e in pool}
    return [by[k] for k in keys if k in by]


def item_layout_out(ecom: EcomSet, it: EcomSetItem) -> dict:
    """单张的镜头与元素（给前端展示与修改）。"""
    info = (ecom.plan or {}).get("styled") or {}
    entry = ((info.get("layout") or {}).get(str(it.idx))) or {}
    shot = shots_mod.get(entry.get("shot"))
    pool = {e["key"]: e for e in info.get("elements") or []}
    return {"shot": shot.key if shot else "", "shot_name": shot.name if shot else "",
            "elements": [{"key": k, "name": pool[k]["name"]} for k in entry.get("elements") or [] if k in pool]}


def set_item_layout(ecom: EcomSet, it: EcomSetItem, *, shot: str | None, elements: list[str] | None) -> None:
    """修改单张的镜头 / 元素（镜头只对合成槽位有效；元素只能从本套元素池里选）。"""
    from app.services.ecom_set.presets import SLOTS

    plan = dict(ecom.plan or {})
    info = dict(plan.get("styled") or {})
    layout = {k: dict(v) for k, v in (info.get("layout") or {}).items()}
    entry = layout.get(str(it.idx), {})
    slot = SLOTS.get(it.slot)
    if shot and slot is not None and slot.ref == "composite" and it.slot != "goods_lineup":
        if shot not in shots_mod.SHOTS:
            raise ValueError("不支持的镜头")
        entry["shot"] = shot
    if elements is not None:
        pool = {e["key"] for e in info.get("elements") or []}
        entry["elements"] = [k for k in dict.fromkeys(elements) if k in pool][:6]
    layout[str(it.idx)] = entry
    info["layout"] = layout
    plan["styled"] = info
    ecom.plan = plan


async def add_set_elements(db: AsyncSession, user: User, set_id: int, keys: list[str]) -> EcomSet:
    """往本套元素池里追加元素（如刚新建的「我的元素」），之后单张即可选用。"""
    from app.services.ecom_set import elements

    from app.services.ecom_set.service import get_set

    ecom = await get_set(db, user, set_id)
    plan = dict(ecom.plan or {})
    info = dict(plan.get("styled") or {})
    pool = list(info.get("elements") or [])
    have = {e["key"] for e in pool}
    for e in elements.snapshot(await elements.get_many(db, user, keys)):
        if e["key"] not in have:
            pool.append(e)
    info["elements"] = pool[:24]
    plan["styled"] = info
    ecom.plan = plan
    await db.commit()
    return await get_set(db, user, set_id)
