"""图片生成可选资产库图片作参考：参数解析与资产落地为参考文件（纯单测，资产查询打桩）。"""

import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services import studio_references as refs


# 只接受 reference_image + asset_id，去重保序
def test_parse_image_asset_references_ok():
    raw = json.dumps([{"role": "reference_image", "asset_id": 3}, {"role": "reference_image", "asset_id": 2},
                      {"role": "reference_image", "asset_id": 3}])
    assert refs.parse_image_asset_references(raw) == [3, 2]


# 上传索引或首尾帧角色都不允许出现在图片资产参考里
@pytest.mark.parametrize("item", [{"role": "reference_image", "file_index": 0}, {"role": "first_frame", "asset_id": 1}])
def test_parse_image_asset_references_rejects(item):
    with pytest.raises(ValueError):
        refs.parse_image_asset_references(json.dumps([item]))


# 超过 9 张报错
def test_parse_image_asset_references_limit():
    raw = json.dumps([{"role": "reference_image", "asset_id": i} for i in range(1, 11)])
    with pytest.raises(ValueError):
        refs.parse_image_asset_references(raw)


# 本地 /static 资产被复制成本次参考文件，原文件不动
async def test_materialize_copies_local_asset(tmp_path, monkeypatch):
    from app.services import storage, studio_tools

    src = tmp_path / "model.png"
    buf = BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    src.write_bytes(buf.getvalue())

    async def fake_params(db, user, items, files=None):
        return {"reference_images": ["/static/generated/x/model.png"]}

    monkeypatch.setattr(refs, "reference_params", fake_params)
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: src)
    monkeypatch.setattr(studio_tools, "tools_dir", lambda uid: tmp_path)
    out = await refs.materialize_image_assets(None, SimpleNamespace(id=1), [7])
    assert len(out) == 1 and out[0].exists() and out[0] != src
    assert out[0].read_bytes() == src.read_bytes()
    assert src.exists()


# 资产文件丢失时给出中文提示
async def test_materialize_missing_file(tmp_path, monkeypatch):
    from app.services import storage

    async def fake_params(db, user, items, files=None):
        return {"reference_images": ["/static/generated/x/gone.png"]}

    monkeypatch.setattr(refs, "reference_params", fake_params)
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: tmp_path / "gone.png")
    with pytest.raises(ValueError, match="不存在"):
        await refs.materialize_image_assets(None, SimpleNamespace(id=1), [7])
