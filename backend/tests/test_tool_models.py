"""图片生成「模特库」：加入/移出、唯一默认、只能用本人图片、模特须为参考图 1、提示词身份约束。"""

import pytest

from app.models_drama import DramaAsset, DramaProject
from app.services import tool_models as tm
from tests.conftest import make_user


# 模特须在第 1 位
def test_check_model_first():
    tm.check_model_first([5, 6], 5)
    with pytest.raises(ValueError, match="第 1 张"):
        tm.check_model_first([6, 5], 5)
    with pytest.raises(ValueError, match="第 1 张"):
        tm.check_model_first([], 5)


# 选了模特才加身份约束，原文保留在后面
def test_model_prompt():
    assert tm.model_prompt("颈前打结", {"model_asset_id": 3}).startswith("【固定模特】")
    assert tm.model_prompt("颈前打结", {"model_asset_id": 3}).endswith("颈前打结")
    assert tm.model_prompt("颈前打结", {}) == "颈前打结"


async def _image(db, project, name):
    asset = DramaAsset(project_id=project.id, type="image", asset_type="image", name=name,
                       url=f"/static/generated/{name}.png", params={"role": "result"})
    db.add(asset)
    await db.flush()
    return asset


# 加入模特库、默认唯一、移出；别人的图片不能操作
async def test_update_and_list_models(db_session):
    user, other = await make_user(db_session), await make_user(db_session)
    project = DramaProject(user_id=user.id, title="模特", params={})
    foreign = DramaProject(user_id=other.id, title="别人", params={})
    db_session.add_all([project, foreign])
    await db_session.flush()
    a, b = await _image(db_session, project, "a"), await _image(db_session, project, "b")
    x = await _image(db_session, foreign, "x")

    assert await tm.list_models(db_session, user.id) == []
    await tm.update_model(db_session, user.id, a.id, is_model_flag=True, is_default=True)
    await tm.update_model(db_session, user.id, b.id, is_model_flag=True, is_default=False)
    rows = await tm.list_models(db_session, user.id)
    assert [r.id for r in rows] == [a.id, b.id] and tm.is_default_model(rows[0])
    assert a.params["role"] == "result"                       # 原有 params 保留

    await tm.update_model(db_session, user.id, b.id, is_model_flag=True, is_default=True)
    rows = await tm.list_models(db_session, user.id)
    assert rows[0].id == b.id and [tm.is_default_model(r) for r in rows] == [True, False]

    await tm.update_model(db_session, user.id, b.id, is_model_flag=False, is_default=False)
    assert [r.id for r in await tm.list_models(db_session, user.id)] == [a.id]
    assert (await tm.get_model(db_session, user.id, a.id)).id == a.id
    with pytest.raises(ValueError, match="还没有加入模特库"):
        await tm.get_model(db_session, user.id, b.id)
    with pytest.raises(ValueError, match="不是你的图片资产"):
        await tm.update_model(db_session, user.id, x.id, is_model_flag=True, is_default=True)
