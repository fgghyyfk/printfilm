"""Public short-video styles without changing stored legacy templates."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.schemas import TemplateDetailOut

if TYPE_CHECKING:
    from app.models import Template


CREATION_TEMPLATE_IDS = ("anim_3d", "soft_anime", "kids_flat", "ink_guofeng")
DEFAULT_CREATION_TEMPLATE_ID = CREATION_TEMPLATE_IDS[0]

_PUBLIC_PRESENTATION = {
    "anim_3d": ("3D 动画", "圆润的三维角色与柔和光影，适合知识讲解和故事短片。", ["3D", "动画"]),
    "soft_anime": ("二维动漫", "干净线稿与柔和赛璐璐色彩，适合青春故事和情感表达。", ["二维", "动漫"]),
    "kids_flat": ("绘本插画", "柔和粉彩与亲切的插画角色，适合儿童科普和温暖故事。", ["绘本", "插画"]),
    "ink_guofeng": ("国风水墨", "水墨留白与写意笔触，适合历史文化和诗意故事。", ["国风", "水墨"]),
}


def normalize_creation_template(template: Template) -> dict:
    """Return public defaults; do not mutate ORM rows used by old projects."""
    data = TemplateDetailOut.model_validate(template).model_dump()
    presentation = _PUBLIC_PRESENTATION.get(template.id)
    if presentation:
        name, description, category = presentation
        data.update(
            name=name,
            description=description,
            category=list(category),
            default_ratio="9:16",
            sort_order=CREATION_TEMPLATE_IDS.index(template.id),
        )
    return data
