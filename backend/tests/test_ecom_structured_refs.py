"""电商结构化参考图输入与旧通路兼容性。"""
import logging

import pytest

from app.services.tokenfree_image import (
    build_tokenfree_image_body,
    build_tokenfree_openai_image_body,
)


# 新通路以图片块携带产品与风格，逐图写明用途。
def test_responses_structured_refs():
    body = build_tokenfree_image_body(
        model="gpt-image-2", prompt="精修", size="2k", aspect_ratio="3:4",
        ref_urls=["data:image/png;base64,AAAA", "https://cdn.example/p2.png"],
        style_ref_urls=["https://cdn.example/style.png"], structured_refs=True,
    )
    content = body["input"][0]["content"]
    assert body["input"][0]["role"] == "user"
    images = [b for b in content if b["type"] == "input_image"]
    assert [b["image_url"] for b in images] == [
        "data:image/png;base64,AAAA", "https://cdn.example/p2.png", "https://cdn.example/style.png",
    ]
    texts = [b["text"] for b in content if b["type"] == "input_text"]
    assert "3:4" in texts[0]
    assert "产品主体" in texts[1] and "logo" in texts[1]
    assert "图2" in texts[2] and "产品主体" in texts[2]
    assert "图3" in texts[3] and "风格" in texts[3]


# 未显式开启的历史调用继续发送纯文本。
def test_refs_default_remains_text():
    args = dict(model="gpt-image-2", prompt="精修", ref_urls=["https://cdn.example/p.png"])
    assert build_tokenfree_image_body(**args) == build_tokenfree_image_body(**args, structured_refs=False)
    assert isinstance(build_tokenfree_image_body(**args)["input"], str)


# 图片生成接口保持已有文本格式并记录结构化降级说明。
def test_openai_structured_falls_back_with_log(caplog):
    args = dict(model="gpt-image-2.5", prompt="精修", ref_urls=["https://cdn.example/p.png"])
    with caplog.at_level(logging.INFO):
        body = build_tokenfree_openai_image_body(**args, structured_refs=True)
    assert body == build_tokenfree_openai_image_body(**args)
    assert isinstance(body["prompt"], str)
    assert "https://cdn.example/p.png" in body["prompt"]
    assert "结构化" in caplog.text


# 开关打开但没有参考图时保留纯文本输入。
def test_structured_without_refs_remains_text():
    body = build_tokenfree_image_body(model="gpt-image-2", prompt="精修", structured_refs=True)
    assert body["input"] == "精修"


# 网关显式开启新模式时必须转发开关，默认调用继续兼容原签名。
@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_gen_image_forwards_opt_in(monkeypatch, enabled):
    from app.services.ark import ArkGateway, ImageResult

    gateway = ArkGateway()
    monkeypatch.setattr(ArkGateway, "mock", property(lambda self: False))

    # 截住上游请求，验证开关与产品引用一路转发而不联网。
    async def once(prompt, refs, **kwargs):
        assert refs == ["data:image/png;base64,AAAA"]
        assert kwargs.get("structured_refs", False) is enabled
        return ImageResult(local_url="/static/test.png")

    monkeypatch.setattr(gateway, "_seedream_once", once)
    result = await gateway.gen_image(
        "精修产品", ref_urls=["data:image/png;base64,AAAA"], structured_refs=enabled,
    )
    assert result.local_url == "/static/test.png"


# 只在结构化通路将本地图片编码，公网引用保持原地址。
@pytest.mark.asyncio
async def test_prepare_structured_local_refs(monkeypatch, tmp_path):
    from PIL import Image
    from app.services import storage
    from app.services.tokenfree_image import prepare_tokenfree_structured_refs

    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    Image.new("RGB", (8, 8), "red").save(tmp_path / "product.png")
    refs = await prepare_tokenfree_structured_refs([
        "/static/product.png", "https://cdn.example.com/style.png",
    ])
    assert refs[0].startswith("data:image/jpeg;base64,")
    assert refs[1] == "https://cdn.example.com/style.png"


# 实际网关按上游协议选择编码方式，GPT2.5 文本降级不得包含 Base64。
@pytest.mark.asyncio
@pytest.mark.parametrize("model,structured", [("gpt-image-2", True), ("gpt-image-2.5", True), ("gpt-image-2", False)])
async def test_gateway_protocol_keeps_fallback_url(monkeypatch, tmp_path, model, structured):
    from PIL import Image
    from app.config import get_settings
    from app.services import ark, storage

    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    Image.new("RGB", (8, 8), "red").save(tmp_path / "product.png")
    gateway = ark.ArkGateway(get_settings().model_copy(update={"model_image": model}))
    monkeypatch.setattr(gateway, "_resolve_ark_route", lambda *args: None)
    monkeypatch.setattr(gateway, "_route_headers", lambda *args: {})
    captured = {}

    # 在真实请求体构造后拦截 HTTP，确保无需模型 Key 和网络。
    async def capture(client, method, url, **kwargs):
        captured.update({"url": url, "body": kwargs["json"]})
        raise RuntimeError("request captured")

    monkeypatch.setattr(ark, "observed_request", capture)
    ref = storage.to_public_url("/static/product.png")
    with pytest.raises(RuntimeError, match="request captured"):
        await gateway._seedream_once("精修", [ref], project_id=0, shot_no=1, size="2K", structured_refs=structured, model=model)
    body = captured["body"]
    if model == "gpt-image-2.5":
        assert captured["url"].endswith("/images/generations")
        assert ref in body["prompt"] and "base64" not in body["prompt"]
    elif structured:
        assert captured["url"].endswith("/responses")
        assert body["input"][0]["content"][2]["image_url"].startswith("data:image/jpeg;base64,")
    else:
        # 普通通路沿用线上策略（参考图以文本 URL 传递），结构化模式的压缩内嵌不得影响它。
        assert captured["url"].endswith("/responses")
        assert isinstance(body["input"], str) and "base64" not in body["input"]
