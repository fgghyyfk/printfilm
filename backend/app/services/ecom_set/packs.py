"""电商图元素包：一键套用一组搭配好的元素。风格包（柑橘、木质、花香…）+ 每个节日的节日包。"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.ecom_set import festivals

# 风格包：key → (名称, 元素 key 不含 el_ 前缀)
STYLE_PACKS: dict[str, tuple[str, str]] = {
    "citrus": ("柑橘清新", "orange_slice lemon_slice grapefruit bergamot mint leaf_shadow"),
    "woody": ("森林木质", "pine_twig moss old_wood pinecone fern rock"),
    "floral": ("花香柔美", "rose_petal peony jasmine camellia silk glass_vase"),
    "water": ("水润清透", "water_ripple water_drops splash pebble glass_dish mist"),
    "herbal": ("植萃草本", "gotu_kola tea_leaf_fresh mint rosemary eucalyptus wood_slice"),
    "tea": ("东方茶韵", "green_tea tea_cup bamboo_leaf pottery_vase paper window_shadow"),
    "milky": ("奶香甜美", "milk oat honey almond vanilla linen"),
    "ocean": ("海洋矿物", "shell sand salt driftwood coral water_ripple"),
    "luxe": ("高级质感", "marble_block brass_tray velvet crystal rim_light amber"),
}


def style_pack_keys(key: str) -> list[str]:
    return [f"el_{k}" for k in STYLE_PACKS[key][1].split()]


async def list_packs(db: AsyncSession, user: User, *, scene_key: str = "", today: date | None = None) -> dict:
    """全部元素包（元素已解析成可见元素）：本场景的节日包、风格包、按日期排好的节日包。"""
    from app.services.ecom_set import elements

    fest_list = festivals.ordered(today)
    wanted = [k for key in STYLE_PACKS for k in style_pack_keys(key)]
    wanted += [k for f in fest_list for k in festivals.pack_keys(f)]
    rows = {r.key: elements.to_out(r) for r in await elements.get_many(db, user, wanted, limit=None)}

    def resolve(keys: list[str]) -> list[dict]:
        return [rows[k] for k in keys if k in rows]

    style = [{"key": f"style_{k}", "name": name, "kind": "style", "festival": None,
              "elements": resolve(style_pack_keys(k))} for k, (name, _) in STYLE_PACKS.items()]
    fest = [{"key": f"fest_{f.key}", "name": f.name, "kind": "festival", "festival": festivals.festival_out(f, today),
             "elements": resolve(festivals.pack_keys(f))} for f in fest_list]
    scene_fest = festivals.of_scene(scene_key)
    return {"packs": style + fest, "scene_pack": f"fest_{scene_fest.key}" if scene_fest else None}
