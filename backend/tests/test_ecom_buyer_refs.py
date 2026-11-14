"""验证 E3 的产品、模特、场景和成图参考经过真实请求体后仍保持完整。"""
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services import ark, storage
from app.services.ecom_jobs import jobs, kinds


# handler 必须按提示词顺序把全部角色传到网关，不能误用设计风格通路。
async def test_buyer_provider_roles(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "ark_mock", False)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    captured = {}
    roles = ["product"] * 9 + ["model", "scene", "source"]
    inputs = [{"role": role, "url": f"https://example.com/{idx}.png"} for idx, role in enumerate(roles)]

    # 拦截实际模型边界，只观察参数。
    async def gen_image(prompt, negative, refs, **kwargs):
        captured.update(refs=refs, prompt=prompt, **kwargs)
        return ark.ImageResult(local_url="/static/out.png")

    # 不下载测试输出。
    async def ensure_local(url, dest):
        return dest

    monkeypatch.setattr(ark, "get_ark", lambda: SimpleNamespace(gen_image=gen_image))
    monkeypatch.setattr(storage, "ensure_local_media", ensure_local)
    job = SimpleNamespace(user_id=1, id=1, kind="buyer_show", params={})
    item = SimpleNamespace(idx=0, prompt=kinds.build_item_prompt("buyer_show", {}, inputs, 0), ratio="3:4", size="90x120", inputs=inputs)
    await jobs.generate_image(None, job, item)
    assert captured["reference_roles"] == roles
    assert captured["refs"] == [ref["url"] for ref in inputs]
    assert captured["structured_refs"] is True
    assert "style_ref_urls" not in captured


# 两条通路都保留十二张图；结构化模式不能将模特误标成产品主体。
@pytest.mark.parametrize("model", ["gpt-image-2", "gpt-image-2.5"])
async def test_buyer_gateway_all_roles(monkeypatch, model):
    gateway = ark.ArkGateway(get_settings().model_copy(update={"model_image": model}))
    monkeypatch.setattr(gateway, "_resolve_ark_route", lambda *args: None)
    monkeypatch.setattr(gateway, "_route_headers", lambda *args: {})
    roles = ["product"] * 9 + ["model", "scene", "source"]
    refs = [f"https://example.com/{idx}.png" for idx in range(12)]
    captured = {}

    # 在协议请求体形成后拦截，禁止真实上游调用。
    async def capture(client, method, url, **kwargs):
        captured.update(kwargs["json"])
        raise RuntimeError("captured")

    monkeypatch.setattr(ark, "observed_request", capture)
    with pytest.raises(RuntimeError, match="captured"):
        await gateway._seedream_once("模特仅作外形参考，不复制真人身份", refs, project_id=0, shot_no=1,
                                     size="2K", structured_refs=True, reference_roles=roles, model=model)
    if model == "gpt-image-2.5":
        assert all(url in captured["prompt"] for url in refs)
        assert "图10（model）" in captured["prompt"] and "图12（source）" in captured["prompt"]
    else:
        blocks = captured["input"][0]["content"]
        assert [block["image_url"] for block in blocks if block["type"] == "input_image"] == refs
        assert "model" in blocks[19]["text"]
        assert "产品主体" not in blocks[19]["text"]


# 生成接口自身也应传递角色，而非只在底层单次调用时可用。
async def test_buyer_gateway_public_method_roles(monkeypatch):
    gateway = ark.ArkGateway(get_settings().model_copy(update={"ark_mock": False}))
    captured = {}

    # 捕获统一网关的单次请求，避免付费和重试。
    async def once(prompt, refs, **kwargs):
        captured.update(kwargs)
        return ark.ImageResult(local_url="/static/mock.png")

    monkeypatch.setattr(gateway, "_ark_api_key", lambda: "test-key-never-used")
    monkeypatch.setattr(gateway, "_seedream_once", once)
    await gateway.gen_image("模特仅作外形参考，不复制真人身份", ref_urls=["https://example.com/model.png"],
                            structured_refs=True, reference_roles=["model"])
    assert captured["reference_roles"] == ["model"]
