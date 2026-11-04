"""火山方舟国内站（北京）作为 Ark 原生渠道：白名单与配置校验（纯单测）。"""

import pytest

from app.schemas_routing import DefaultModels, SystemModelChannel, SystemModelChannelIn
from app.services import provider_profiles
from app.services.provider_profiles import VOLC_ARK_BEIJING, is_byteplus, normalize_profiles


# 国际站区域集合不能混入国内站，协议支持范围另行定义
def test_byteplus_regions_are_separate_from_ark_native_protocol():
    assert VOLC_ARK_BEIJING not in provider_profiles.BYTEPLUS_BASES
    assert len(provider_profiles.BYTEPLUS_BASES) == 2
    assert provider_profiles.ARK_NATIVE_BASES == provider_profiles.BYTEPLUS_BASES | {VOLC_ARK_BEIJING}


# 火山北京地址按 Ark 原生协议识别；相似但非官方的地址仍被拒
def test_volc_beijing_is_ark_native():
    assert provider_profiles.is_ark_native(VOLC_ARK_BEIJING)
    assert is_byteplus(VOLC_ARK_BEIJING)
    assert is_byteplus(VOLC_ARK_BEIJING + "/")


# 原生协议只认可精确白名单，不能被相似域名、用户信息或路径绕过
@pytest.mark.parametrize("base", [
    "https://ark.cn-beijing.volces.com.evil.example/api/v3",
    "https://ark.cn-beijing.volces.com@evil.example/api/v3",
    "https://evil.example/ark.cn-beijing.volces.com/api/v3",
    "https://ark.ap-southeast.bytepluses.com.evil.example/api/v3",
    "http://ark.cn-beijing.volces.com/api/v3",
    "https://ark.cn-beijing.volces.com/api/v3/other",
])
def test_similar_ark_urls_are_not_native_or_accepted(base):
    assert not provider_profiles.is_ark_native(base)
    assert not is_byteplus(base)
    item = SystemModelChannelIn(
        id="x", name="x", base_url=base, api_key="k", enabled=True,
        advanced_config=dict(provider="byteplus", text_model="m"),
    )
    with pytest.raises(ValueError, match="官方区域"):
        normalize_profiles([item])


# 选火山北京区域的配置能通过校验，模型列表取各能力填写的 ID
def test_normalize_accepts_volc_profile():
    item = SystemModelChannelIn(
        id="volc-a", name="火山方舟", base_url=VOLC_ARK_BEIJING, api_key="k", enabled=True,
        advanced_config=dict(
            provider="byteplus", text_model="deepseek-v4-flash-ga-260731",
            image_model="doubao-seedream-5-0-pro-260628", video_model="",
        ),
    )
    out = normalize_profiles([item])
    assert out[0].base_url == VOLC_ARK_BEIJING
    assert out[0].models == ["deepseek-v4-flash-ga-260731", "doubao-seedream-5-0-pro-260628"]


# 非官方区域地址依旧报错
def test_normalize_rejects_unknown_ark_region():
    item = SystemModelChannelIn(
        id="x", name="x", base_url="https://ark.example.com/api/v3", api_key="k", enabled=True,
        advanced_config=dict(provider="byteplus", text_model="m"),
    )
    with pytest.raises(ValueError):
        normalize_profiles([item])


# 受控 HTTP 覆盖北京实际图片创建、视频创建和查询，避免只验证判定函数
async def test_beijing_uses_native_image_and_video_http(monkeypatch, tmp_path):
    import json
    import httpx
    from app.config import Settings
    from app.services import ark, model_settings

    # 与后台保存相同的配置归一化及逻辑路由，不连接数据库
    item = SystemModelChannelIn(
        id="volc-a", name="火山方舟", base_url=VOLC_ARK_BEIJING,
        api_key="volc-test", enabled=True,
        advanced_config=dict(provider="byteplus", image_model="ep-image", video_model="ep-video"),
    )
    channel = SystemModelChannel(**normalize_profiles([item])[0].model_dump(), has_api_key=True)
    logical, defaults = provider_profiles.profile_state([channel], DefaultModels())
    overlay = provider_profiles.profile_overlay({}, [channel], defaults)
    monkeypatch.setattr(model_settings, "_routing_snapshot", model_settings.RoutingSnapshot(
        channels=[channel], logical_models=logical, default_models=defaults,
    ))
    gateway = ark.ArkGateway(Settings(**overlay, ark_mock=False))
    calls = []

    # 传输层只接收北京原生端点，不进行公网调用
    def handler(request):
        assert request.headers["authorization"] == "Bearer volc-test"
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, str(request.url), body))
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/image.png"}]})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "volc-task"})
        return httpx.Response(200, json={
            "id": "volc-task", "status": "succeeded", "content": {"video_url": "https://cdn.example.com/video.mp4"},
        })

    # 用 MockTransport 保留真实请求构造和解析
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)))

    # 图片下载只落临时目录且不能发送渠道凭据
    async def download(url, dest, **kw):
        assert url == "https://cdn.example.com/image.png"
        assert not kw.get("headers")
        dest.write_bytes(b"fake-image")

    monkeypatch.setattr(ark.storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(ark.storage, "download_to", download)
    monkeypatch.setattr(ark.storage, "publish_local", lambda dest, **kw: str(dest))
    image = await gateway._seedream_once("portrait", [], project_id=0, shot_no=1, size="2K")
    task_id = await gateway.gen_video_seedance_body({
        "model": "seedance-2.5", "content": [{"type": "text", "text": "hello"}], "duration": 5,
    })
    result = await gateway.fetch_task_once(task_id)
    assert image.remote_url.endswith("image.png")
    assert result.status == "succeeded" and result.url.endswith("video.mp4")
    assert [(method, url) for method, url, _ in calls] == [
        ("POST", VOLC_ARK_BEIJING + "/images/generations"),
        ("POST", VOLC_ARK_BEIJING + "/contents/generations/tasks"),
        ("GET", VOLC_ARK_BEIJING + "/contents/generations/tasks/volc-task"),
    ]
    assert calls[0][2]["model"] == "ep-image" and "prompt" in calls[0][2]
    assert calls[1][2]["model"] == "ep-video" and "content" in calls[1][2]
    assert "metadata" not in calls[1][2] and "prompt" not in calls[1][2]
