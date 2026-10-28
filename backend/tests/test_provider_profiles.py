"""手动渠道配置：保存、隔离与真实协议路径的回归。"""
import pytest
from sqlalchemy import select
from app.schemas_routing import AdminRoutingSettingsPatch, SystemModelChannel
from app.models_settings import SystemModelChannelRow
from app.services import model_settings
from app.services.tokenfree_gateway import resolve_tokenfree_api_key
from app.services.ark import ArkGateway
from app.config import Settings

AP = 'https://ark.ap-southeast.bytepluses.com/api/v3'


def profiles():
    """同一份请求包含独立 TokenFree / BytePlus 凭据。"""
    return [dict(id='tokenfree', name='TokenFree', base_url='https://www.tokenfree.com/v1', api_key='tf-test', enabled=False),
            dict(id='byteplus-a', name='BytePlus A', base_url=AP, api_key='bp-test', enabled=True,
                 advanced_config=dict(provider='byteplus', text_model='ep-text', image_model='ep-image', video_model='ep-video'))]


@pytest.fixture(autouse=True)
def isolate():
    """每例恢复进程级缓存。"""
    snapshot = model_settings._routing_snapshot
    overlay = dict(model_settings._overlay)
    yield
    model_settings._routing_snapshot = snapshot
    model_settings._overlay.clear()
    model_settings._overlay.update(overlay)
    model_settings.reload_settings()


async def test_save_switch_restart_and_preserve_separate_keys(db_session):
    """保存两个配置，切换和重载均不丢 Key；响应永不回显。"""
    out, _ = await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=profiles()))
    assert len(out.system_channels) == 2
    assert [c.id for c in out.system_channels if c.enabled] == ['byteplus-a']
    assert all(c.api_key == '' for c in out.system_channels)
    await model_settings.load_model_settings_cache(db_session)
    from app.services.logical_model_router import resolve_logical_model
    for cap in ('text', 'image', 'video'):
        route = resolve_logical_model(cap, '')
        assert (route.channel_id, route.base_url, route.api_key, route.upstream_model) == ('byteplus-a', AP, 'bp-test', 'ep-'+cap)
    assert resolve_tokenfree_api_key() == ''
    assert resolve_logical_model('audio', '') is None
    items = [c.model_dump(exclude={'has_api_key'}) for c in out.system_channels]
    for c in items:
        c['api_key'] = None
        c['enabled'] = c['id'] == 'tokenfree'
    await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=items))
    await model_settings.load_model_settings_cache(db_session)
    route = resolve_logical_model('image', '')
    assert route.channel_id == 'tokenfree' and route.api_key == 'tf-test'
    rows = list((await db_session.execute(select(SystemModelChannelRow))).scalars())
    assert model_settings._decrypt_secret(next(r for r in rows if r.id == 'byteplus-a').api_key_ciphertext) == 'bp-test'
    assert all('bp-test' not in (r.api_key_ciphertext or '') for r in rows)


async def test_reject_multiple_active_without_mutating_database(db_session):
    items = profiles()
    items[0]['enabled'] = True
    with pytest.raises(ValueError, match='一个'):
        await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=items))
    assert not list((await db_session.execute(select(SystemModelChannelRow))).scalars())


async def test_clear_key_never_reuses_previous_channel_or_env(db_session):
    out, _ = await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=profiles()))
    items = [c.model_dump(exclude={'has_api_key'}) for c in out.system_channels]
    for c in items:
        c['api_key'] = None
        c['clear_api_key'] = c['enabled']
    await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=items))
    assert model_settings.get_overlay_dict()['ark_api_key'] == ''
    assert resolve_tokenfree_api_key() == ''
    with pytest.raises(RuntimeError, match='Key'):
        _ = ArkGateway(Settings(ark_mock=False)).mock


def test_byteplus_uses_native_video_path_and_body():
    """BytePlus 原生协议不能经过 New API /videos 包装。"""
    gw = ArkGateway(Settings(ark_base_url=AP, ark_api_key='bp-test', ark_mock=False))
    assert gw._url('/contents/generations/tasks/task-1') == AP+'/contents/generations/tasks/task-1'
    payload = dict(model='ep-video', content=[dict(type='text', text='hello')], duration=5)
    assert gw._video_json(payload) == payload


def test_tokenfree_never_falls_back_to_active_byteplus_overlay():
    model_settings._routing_snapshot = model_settings.RoutingSnapshot(
        channels=[SystemModelChannel(id='bp', name='BP', base_url=AP, api_key='bp-test', has_api_key=True)],
        logical_models=[], default_models=model_settings.DefaultModels())
    model_settings._refresh_overlay({'flat':dict(openai_api_key='bp-test', ark_api_key='bp-test')})
    model_settings.reload_settings()
    assert resolve_tokenfree_api_key() == ''


@pytest.mark.parametrize('status', ['pending', 'awaiting_poll', 'awaiting_review', 'cancel_requested'])
async def test_unfinished_task_blocks_profile_changes(db_session, status):
    """轮询、待审和取消中的任务均不得换 Key 或地址。"""
    from conftest import make_user, make_task
    user = await make_user(db_session)
    await make_task(db_session, user, status=status)
    with pytest.raises(ValueError, match='未结束任务'):
        await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=profiles()))
    assert not list((await db_session.execute(select(SystemModelChannelRow))).scalars())


async def test_second_tokenfree_key_is_used_only_when_selected(db_session):
    """多个同平台 Key 仍然只使用手动选择的一条。"""
    items = profiles()[:1]
    items.append(dict(id='tokenfree-second', name='TokenFree second', base_url='https://www.tokenfree.com/v1', api_key='tf-second', enabled=True))
    await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=items))
    assert resolve_tokenfree_api_key() == 'tf-second'
    assert ArkGateway()._ark_api_key() == 'tf-second'


async def test_byteplus_native_http_uses_selected_key_and_exact_models(db_session, monkeypatch, tmp_path):
    """受控 HTTP 验证文字、图像、视频查询的完整 URL、Bearer 与模型。"""
    import httpx, json
    from app.services import ark, llm_client
    await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=profiles()))
    calls = []
    def handler(req):
        """模拟 BytePlus 三种返回，不访问公网。"""
        assert req.headers['authorization'] == 'Bearer bp-test'
        assert str(req.url).startswith(AP+'/')
        body = json.loads(req.content) if req.content else {}
        calls.append((req.method, str(req.url), body))
        if req.url.path.endswith('/chat/completions'):
            return httpx.Response(200, json={'choices':[{'message':{'content':'ok'}}]})
        if req.url.path.endswith('/images/generations'):
            return httpx.Response(200, json={'data':[{'url':'https://cdn.example.com/image.png'}]})
        return httpx.Response(200, json={'id':'bp-task', 'status':'succeeded', 'content':{'video_url':'https://cdn.example.com/video.mp4'}})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)))
    async def download(url, dest, **kw):
        """仅模拟公开媒体下载，断言未携带渠道 Key。"""
        assert not kw.get('headers')
        dest.write_bytes(b'fake-image')
    monkeypatch.setattr(ark.storage, 'project_dir', lambda _: tmp_path)
    monkeypatch.setattr(ark.storage, 'download_to', download)
    monkeypatch.setattr(ark.storage, 'publish_local', lambda dest, **kw: str(dest))
    assert await llm_client.chat_completions('system', 'hello') == 'ok'
    gw = ArkGateway()
    image = await gw._seedream_once('portrait', ['https://cdn.example.com/ref.png'], project_id=0, shot_no=1, size='2K')
    result = await gw.fetch_task_once('bp-task')
    assert image.remote_url.endswith('image.png')
    assert result.status == 'succeeded' and result.url.endswith('video.mp4')
    assert calls[0][2]['model'] == 'ep-text'
    assert calls[1][1] == AP+'/images/generations'
    assert calls[1][2]['model'] == 'ep-image' and calls[1][2]['image'].endswith('ref.png')
    assert calls[2][1] == AP+'/contents/generations/tasks/bp-task'


async def test_missing_byteplus_capability_never_uses_tokenfree(db_session):
    """单能力配置不回退另一平台的模型或语音。"""
    items = profiles()
    items[1]['advanced_config']['image_model'] = ''
    await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=items))
    gw = ArkGateway()
    with pytest.raises(RuntimeError, match='image'):
        gw._resolve_ark_route('image', 'seedream-5.0-pro')
    with pytest.raises(RuntimeError, match='独立配音'):
        gw._resolved_audio_model()
    assert model_settings.get_overlay_dict()['model_image'] == ''
    assert model_settings.get_overlay_dict()['model_audio'] == ''


async def test_byteplus_video_create_keeps_native_model_id(db_session, monkeypatch):
    """图生视频和多模态提交均替换前端别名，官方版本号不得被 TokenFree 规范化。"""
    import httpx, json
    from unittest.mock import AsyncMock
    from app.services import seedance_image_aspect
    items = profiles()
    model = 'dreamina-seedance-2-5-260628'
    items[1]['advanced_config']['video_model'] = model
    await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=items))
    calls = []
    def handler(req):
        """只接收原生视频任务创建。"""
        calls.append(json.loads(req.content))
        assert str(req.url) == AP+'/contents/generations/tasks'
        assert req.headers['authorization'] == 'Bearer bp-test'
        return httpx.Response(200, json={'id':'created-task'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(seedance_image_aspect, 'ensure_seedance_compatible_image_url', AsyncMock(return_value='https://cdn.example.com/ref.png'))
    gw = ArkGateway()
    monkeypatch.setattr(gw, '_resolve_image_ref', AsyncMock(return_value='https://cdn.example.com/ref.png'))
    await gw.gen_video_i2v('https://cdn.example.com/ref.png', 'test', 5)
    await gw.gen_video_seedance_body({'model':'seedance-2.5', 'content':[{'type':'text','text':'test'}]})
    assert [body['model'] for body in calls] == [model, model]
    assert all('metadata' not in body and 'content' in body for body in calls)


def test_profile_model_whitespace_is_normalized():
    """粘贴模型 ID 前后空格在保存时去除。"""
    from app.services.provider_profiles import normalize_profiles
    from app.schemas_routing import SystemModelChannelIn
    items = profiles()
    items[1]['advanced_config']['text_model'] = ' ep-text '
    normalized = normalize_profiles([SystemModelChannelIn(**item) for item in items])
    assert normalized[1].advanced_config.text_model == 'ep-text'


def test_tokenfree_switch_clears_byteplus_secondary_model_aliases():
    """辅助 4.5/2.0 模型也不能继承前平台的 endpoint。"""
    from app.services.provider_profiles import profile_overlay
    channels = [SystemModelChannel(id='tf', name='TF', base_url='https://www.tokenfree.com/v1', api_key='tf-test')]
    overlay = profile_overlay(dict(model_image_45='ep-bp-image', model_video_2='ep-bp-video'), channels, model_settings.DefaultModels(image_model='seedream-5.0-pro',video_model='seedance-2.5'))
    assert not overlay['model_image_45'].startswith('ep-')
    assert not overlay['model_video_2'].startswith('ep-')


@pytest.mark.parametrize('url', ['https://www.tokenfree.com/v1/videos/old/content', 'https://cdn.example.com/videos/x/content'])
async def test_media_download_never_sends_byteplus_key_to_other_origins(db_session, monkeypatch, tmp_path, url):
    """下载旧媒体或 CDN 的同形路径，也不能携带当前 BytePlus Key。"""
    from app.services import ark
    await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=profiles()))
    calls = []
    async def download(source, dest, **kw):
        calls.append(kw.get('headers'))
    async def resolve(source, **kw):
        assert kw.get('api_key') != 'bp-test'
        return source
    monkeypatch.setattr(ark.storage, 'download_to', download)
    monkeypatch.setattr(ark, 'resolve_tokenfree_media_download_url', resolve)
    await ArkGateway().download_result_media(url, tmp_path/'video.mp4')
    assert calls == [None]
