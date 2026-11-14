"""火山方舟国内站（北京）作为 Ark 原生渠道：白名单与配置校验（纯单测）。"""

import pytest

from app.schemas_routing import SystemModelChannelIn
from app.services import provider_profiles
from app.services.provider_profiles import VOLC_ARK_BEIJING, is_byteplus, normalize_profiles


# 火山北京地址按 Ark 原生协议识别；相似但非官方的地址仍被拒
def test_volc_beijing_is_ark_native():
    assert is_byteplus(VOLC_ARK_BEIJING)
    assert is_byteplus(VOLC_ARK_BEIJING + "/")
    assert not is_byteplus("https://ark.cn-beijing.volces.com.evil.example/api/v3")


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
