"""图片生成的「模特库」：把个人图片资产标记为模特，可设一个默认模特。

标记存在资产 params 上（model_role="model"、model_default=true），不新增表或列。
生成时所选模特固定作为参考图 1，并在提示词前加一句身份约束，保证每次都是同一个人。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_drama import DramaAsset, DramaProject
from app.services.studio_references import asset_kind_expression, personal_asset_filters

MODEL_ROLE = "model"
MODEL_PROMPT_PREFIX = (
    "【固定模特】参考图1是本次指定的模特：画面中的人物必须是图1这位模特本人，"
    "脸型、五官、发型、肤色与气质保持完全一致，不要换人、不要改变长相；其余参考图按各自用途使用。\n"
)


def is_model(asset: DramaAsset) -> bool:
    """资产是否已加入模特库。"""
    return (asset.params or {}).get("model_role") == MODEL_ROLE


def is_default_model(asset: DramaAsset) -> bool:
    """资产是否为默认模特。"""
    return is_model(asset) and bool((asset.params or {}).get("model_default"))


async def _personal_images(db: AsyncSession, user_id: int, asset_id: int | None = None) -> list[DramaAsset]:
    """读取本人个人空间里的图片资产（可按 id 精确取一条）。"""
    query = (select(DramaAsset).join(DramaProject, DramaProject.id == DramaAsset.project_id)
             .where(*personal_asset_filters(user_id), asset_kind_expression() == "image"))
    if asset_id is not None:
        query = query.where(DramaAsset.id == asset_id)
    return list((await db.execute(query.order_by(DramaAsset.id.desc()))).scalars())


async def list_models(db: AsyncSession, user_id: int) -> list[DramaAsset]:
    """本人的模特库：默认模特排第一，其余按加入先后（新的在前）。"""
    rows = [a for a in await _personal_images(db, user_id) if is_model(a)]
    return sorted(rows, key=lambda a: (not is_default_model(a), -a.id))


async def get_model(db: AsyncSession, user_id: int, asset_id: int) -> DramaAsset:
    """取本人模特库里的一条；不是本人图片或未加入模特库时报错。"""
    rows = await _personal_images(db, user_id, asset_id)
    if not rows:
        raise ValueError("model_asset_id: 模特图不存在或不是你的图片资产")
    if not is_model(rows[0]):
        raise ValueError("model_asset_id: 这张图还没有加入模特库")
    return rows[0]


async def update_model(db: AsyncSession, user_id: int, asset_id: int, *, is_model_flag: bool,
                       is_default: bool) -> DramaAsset:
    """加入/移出模特库，或设为默认模特（同一用户只保留一个默认）。不提交事务。"""
    rows = await _personal_images(db, user_id, asset_id)
    if not rows:
        raise ValueError("模特图不存在或不是你的图片资产")
    asset = rows[0]
    params = dict(asset.params or {})
    if is_model_flag:
        params["model_role"] = MODEL_ROLE
        params["model_default"] = bool(is_default)
    else:
        params.pop("model_role", None)
        params.pop("model_default", None)
    if is_model_flag and is_default:
        for other in await list_models(db, user_id):
            if other.id != asset.id and is_default_model(other):
                other.params = {**(other.params or {}), "model_default": False}
    asset.params = params
    await db.flush()
    return asset


def check_model_first(asset_ids: list[int], model_asset_id: int) -> None:
    """模特必须是参考图第 1 位（提示词约束写的是「图1」，前端也按此顺序计算色板序号）。"""
    if not asset_ids or asset_ids[0] != model_asset_id:
        raise ValueError("model_asset_id: 模特图必须是第 1 张参考图")


def model_prompt(prompt: str, params: dict | None) -> str:
    """生成时的提示词：选了固定模特就在前面加身份约束；历史记录里仍保存用户原文。"""
    return MODEL_PROMPT_PREFIX + (prompt or "") if (params or {}).get("model_asset_id") else (prompt or "")
