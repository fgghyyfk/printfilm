"""模型目录不得把官方 Ark 凭据改发 TokenFree 或复用到未知地址。"""
import httpx
import pytest

from app.schemas_routing import AdminRoutingSettingsPatch
from app.services import model_settings
from app.services.provider_profiles import TOKENFREE_BASE, VOLC_ARK_BEIJING
from app.services.upstream_model_catalog import list_upstream_models


# 捕获真实 HTTP 构造，且每例恢复配置缓存，不调用公网
@pytest.fixture
def catalog_requests(monkeypatch):
    snapshot = model_settings._routing_snapshot
    overlay = dict(model_settings._overlay)
    requests = []
    real_client = httpx.AsyncClient

    # 返回空目录，测试聚焦目标域和凭据归属
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)))
    yield requests
    model_settings._routing_snapshot = snapshot
    model_settings._overlay.clear()
    model_settings._overlay.update(overlay)
    model_settings.reload_settings()


# 真实保存北京配置后，以显式或自动协议拉目录都不能改发 TokenFree
@pytest.mark.parametrize("protocol", ["ark", "auto"])
async def test_saved_beijing_catalog_is_rejected_without_sending_key(db_session, catalog_requests, protocol):
    await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=[dict(
        id="volc-review", name="北京", base_url=VOLC_ARK_BEIJING, api_key="volc-synthetic", enabled=True,
        advanced_config=dict(provider="byteplus", image_model="ep-image"),
    )]))
    with pytest.raises(RuntimeError, match="模型目录"):
        await list_upstream_models(db_session, channel_id="volc-review", protocol=protocol)
    assert catalog_requests == []


# 未保存的 Ark 地址和凭据也应在请求前明确拒绝，而非改投另一上游
@pytest.mark.parametrize("protocol", ["ark", "auto"])
async def test_explicit_beijing_catalog_is_rejected_without_sending_key(catalog_requests, protocol):
    with pytest.raises(RuntimeError, match="模型目录"):
        await list_upstream_models(protocol=protocol, base_url=VOLC_ARK_BEIJING, api_key_override="volc-synthetic")
    assert catalog_requests == []


# 修改已保存渠道的目标地址时，不允许沿用原域名下保存的密钥
@pytest.mark.parametrize("original,override", [(VOLC_ARK_BEIJING, TOKENFREE_BASE), (TOKENFREE_BASE, VOLC_ARK_BEIJING)])
async def test_catalog_base_override_cannot_reuse_saved_key(monkeypatch, catalog_requests, original, override):
    from app.schemas_routing import SystemModelChannel

    # 模拟从数据库解密的单个已保存渠道
    async def load_channels(db, runtime):
        return [SystemModelChannel(id="saved", name="saved", base_url=original, api_key="saved-synthetic")]

    monkeypatch.setattr(model_settings, "_load_channels", load_channels)
    with pytest.raises(RuntimeError, match="重新填写"):
        await list_upstream_models(object(), channel_id="saved", base_url=override)
    assert catalog_requests == []


# 即便明确传入了密钥，也不能把它发给非白名单的相似地址
@pytest.mark.parametrize("base", [
    "https://ark.cn-beijing.volces.com.evil.example/api/v3",
    "https://www.tokenfree.com.evil.example/v1",
    "https://unknown.example/v1",
])
async def test_catalog_rejects_unknown_addresses(catalog_requests, base):
    with pytest.raises(RuntimeError, match="官方"):
        await list_upstream_models(base_url=base, api_key_override="synthetic")
    assert catalog_requests == []


# 显式切换地址且重新填写密钥时，TokenFree 只能收到新密钥
async def test_catalog_override_uses_new_key_on_tokenfree(monkeypatch, catalog_requests):
    from app.schemas_routing import SystemModelChannel

    # 原北京渠道的密钥不得出现在 TokenFree 请求中
    async def load_channels(db, runtime):
        return [SystemModelChannel(id="saved", name="saved", base_url=VOLC_ARK_BEIJING, api_key="volc-synthetic")]

    monkeypatch.setattr(model_settings, "_load_channels", load_channels)
    assert await list_upstream_models(
        object(), channel_id="saved", base_url=TOKENFREE_BASE, api_key_override="tokenfree-new-synthetic",
    ) == []
    assert len(catalog_requests) == 1
    assert str(catalog_requests[0].url) == TOKENFREE_BASE + "/models"
    assert catalog_requests[0].headers["authorization"] == "Bearer tokenfree-new-synthetic"


# 同域的密钥覆盖仍可正常刷新目录
async def test_catalog_key_override_keeps_saved_tokenfree_address(monkeypatch, catalog_requests):
    from app.schemas_routing import SystemModelChannel

    # 返回同域旧密钥，确认请求使用调用者新填的密钥
    async def load_channels(db, runtime):
        return [SystemModelChannel(id="saved", name="saved", base_url=TOKENFREE_BASE, api_key="old-synthetic")]

    monkeypatch.setattr(model_settings, "_load_channels", load_channels)
    assert await list_upstream_models(object(), channel_id="saved", api_key_override="new-synthetic") == []
    assert len(catalog_requests) == 1
    assert str(catalog_requests[0].url) == TOKENFREE_BASE + "/models"
    assert catalog_requests[0].headers["authorization"] == "Bearer new-synthetic"
