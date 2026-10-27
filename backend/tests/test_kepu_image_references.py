"""短视频生图始终携带画风参考，并保留用户和镜间参考。"""
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.config import get_settings
from app.services import storage
from app.models import Project, ProjectStatus, Shot, ShotStatus, Template
from app.services import pipeline
from app.services.ark import ArkGateway
from app.models_tasks import TaskRun
from app.services.media_models import MediaValidationError, get_strategy, planning
from app.services.kepu_image_references import (
    project_image_reference_inputs,
    project_image_references,
    public_image_reference,
)


def _project(**overrides):
    """构造具有默认画风封面的短视频项目。"""
    data = {"template": SimpleNamespace(seedream_config={}, preview_cover="/static/templates/covers/anim_3d.png"),
            "ref_image_url": None}
    return SimpleNamespace(**{**data, **overrides})


@pytest.fixture
def real_references(monkeypatch, tmp_path):
    """使用本地媒体和公网地址模拟真实参考图发布，不访问网络。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ark_mock", False)
    monkeypatch.setattr(settings, "local_media_mock", False)
    monkeypatch.setattr(settings, "public_base_url", "https://media.example.com")
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    monkeypatch.setattr(storage, "republish_url", lambda url, **_: url)
    cover = tmp_path / "templates/covers/anim_3d.png"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(b"reference image")
    return tmp_path


def test_first_shot_gets_template_cover_as_dedicated_style_reference(real_references):
    """模板没配 ref_images 时仍以所选画风封面作参考。"""
    subjects, styles = project_image_references(_project())
    assert subjects == []
    assert styles == ["https://media.example.com/static/templates/covers/anim_3d.png"]


def test_builtin_legacy_empty_cover_uses_bundled_style_board(real_references):
    """旧模板行封面为空时，内置画风仍使用随代码发布的参考图。"""
    project = _project(template=SimpleNamespace(id="anim_3d", preview_cover="", seedream_config={}))
    assert project_image_references(project)[1] == ["https://media.example.com/static/templates/covers/anim_3d.png"]


def test_user_reference_does_not_replace_style_reference(real_references):
    """用户图用于主体，画风图独立保留。"""
    subjects, styles = project_image_references(_project(ref_image_url="https://media.example.com/person.png"))
    assert subjects == ["https://media.example.com/person.png"]
    assert styles == ["https://media.example.com/static/templates/covers/anim_3d.png"]


def test_configured_style_board_takes_priority_over_cover(real_references):
    """后台配置的画风参考优先于展示封面。"""
    project = _project(template=SimpleNamespace(
        seedream_config={"style_ref_urls": ["https://media.example.com/style.png"]},
        preview_cover="/static/templates/covers/anim_3d.png"))
    assert project_image_references(project)[1] == ["https://media.example.com/style.png"]


def test_legacy_subject_images_are_preserved_alongside_explicit_style_board(real_references):
    """新增画风槽位不能吞掉旧模板的角色与产品参考。"""
    project = _project(template=SimpleNamespace(
        seedream_config={"ref_images": ["https://media.example.com/person.png", "https://media.example.com/product.png"],
                         "style_ref_urls": ["https://media.example.com/style.png"]}, preview_cover=""))
    assert project_image_references(project) == (
        ["https://media.example.com/person.png", "https://media.example.com/product.png"],
        ["https://media.example.com/style.png"],
    )


def test_local_reference_prefers_synchronous_publishing(real_references, monkeypatch):
    """中间图显式同步发布，不能把本地 URL 直接交给云端。"""
    published = []
    def publish(url, *, sync):
        """模拟 OSS 发布。"""
        published.append((url, sync))
        return "https://oss.example.com/style.png"
    monkeypatch.setattr(storage, "republish_url", publish)
    assert project_image_references(_project())[1] == ["https://oss.example.com/style.png"]
    assert published == [("/static/templates/covers/anim_3d.png", True)]


def test_inaccessible_style_fails_instead_of_silently_using_text_only(real_references, monkeypatch):
    """真实生成没有可访问参考图时，明确失败而非退回纯提示词。"""
    monkeypatch.setattr(get_settings(), "public_base_url", "http://127.0.0.1:8000")
    with pytest.raises(ValueError, match="画风参考图"):
        project_image_references(_project())


def test_local_reference_cannot_fall_back_to_metadata_server(real_references, monkeypatch):
    """本地图片转绝对地址时同样拒绝私网地址。"""
    monkeypatch.setattr(get_settings(), "public_base_url", "https://169.254.169.254")
    assert public_image_reference("/static/templates/covers/anim_3d.png") is None


@pytest.mark.asyncio
async def test_video_gateway_accepts_public_local_image_without_oss(real_references):
    """图生视频与生图共用可访问的本地图，无 OSS 时也可使用公网 HTTPS 地址。"""
    gateway = object.__new__(ArkGateway)
    assert await gateway._resolve_image_ref("/static/templates/covers/anim_3d.png", prefer_https=True) == (
        "https://media.example.com/static/templates/covers/anim_3d.png"
    )


@pytest.mark.asyncio
async def test_video_gateway_rejects_local_only_reference(real_references, monkeypatch):
    """云端无法读取本机图片时，在提交付费请求前报错。"""
    monkeypatch.setattr(get_settings(), "public_base_url", "http://127.0.0.1:8000")
    gateway = object.__new__(ArkGateway)
    with pytest.raises(RuntimeError, match="公网可访问"):
        await gateway._resolve_image_ref("/static/templates/covers/anim_3d.png", prefer_https=True)


def test_mock_reference_does_not_publish_or_require_media_files(monkeypatch):
    """离线生成允许本地画风图，不触发 OSS 或 HTTP。"""
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "republish_url", lambda *_args, **_kwargs: pytest.fail("mock must not upload"))
    assert project_image_references(_project())[1] == ["/static/templates/covers/anim_3d.png"]


def test_admission_reads_reference_slots_without_publishing(monkeypatch):
    """预占阶段仅采集引用，不上传也不发模型请求。"""
    monkeypatch.setattr(storage, "republish_url", lambda *_args, **_kwargs: pytest.fail("admission must not upload"))
    subjects, styles = project_image_reference_inputs(_project(ref_image_url="/static/person.png"))
    assert subjects == ["/static/person.png"]
    assert styles == ["/static/templates/covers/anim_3d.png"]


@pytest.mark.parametrize("url", [
    "https://www.tokenfree.com/v1/tasks/t1/artifacts/image-0/content",
    "/static/../secret.png", "file:///private/image.png", "https://127.0.0.1/style.png",
    "https://169.254.169.254/style.png", "https://127.0.0.2/style.png",
])
def test_reference_rejects_private_provider_or_unsafe_sources(real_references, url):
    """鉴权产物地址、本地越界路径与私网地址不能泄给下游。"""
    assert public_image_reference(url) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("regenerate", [False, True], ids=["batch", "single-redraw"])
async def test_image_generation_receives_fixed_style_and_previous_subject(real_references, monkeypatch, regenerate):
    """批量生成与单镜重绘都真实向网关传入固定画风、用户图和上一镜图。"""
    template = Template(id="anim_3d", name="3D 动画", style_prefix="圆润三维动画",
                        preview_cover="/static/templates/covers/anim_3d.png", seedream_config={},
                        seedance_config={}, audio_config={})
    shots = [Shot(id=no, shot_no=no, img_prompt=f"同一角色观察天空 {no}", duration=5,
                  status=ShotStatus.PENDING, version=1) for no in (1, 2)]
    project = Project(id=991, user_id=1, template_id=template.id, template=template,
                      pipeline_mode="full", output_ratio="9:16", image_model="seedream-5.0-pro",
                      image_resolution="2K", ref_image_url="https://media.example.com/character.png",
                      character_bible="蓝色外套的男孩", shots=shots, status=ProjectStatus.SCRIPT_READY)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: project)),
                         commit=AsyncMock())

    async def get_row(model, row_id):
        """回读当前项目和分镜。"""
        return project if model is Project else next((shot for shot in shots if shot.id == row_id), None)

    @asynccontextmanager
    async def sessions():
        """离线数据库替身。"""
        yield db

    async def gen_image(_prompt, _negative, _refs, **kwargs):
        """保存图片以供下一镜参考，无模型或网络调用。"""
        path = storage.project_dir(project.id) / f"shot_{kwargs['shot_no']}.png"
        path.write_bytes(b"generated image")
        return SimpleNamespace(local_url=storage.rel_static_url(path),
                               remote_url="https://www.tokenfree.com/v1/tasks/t1/artifacts/image-0/content")

    generate = AsyncMock(side_effect=gen_image)
    db.get = AsyncMock(side_effect=get_row)
    monkeypatch.setattr(storage, "GENERATED_ROOT", real_references / "generated")
    monkeypatch.setattr(get_settings(), "kepu_seedance_native_audio", True)
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(pipeline, "get_ark", lambda: SimpleNamespace(gen_image=generate))
    for method in ("_ensure_not_cancelled", "_set_status", "publish_progress", "_record_seedream_usage"):
        monkeypatch.setattr(pipeline, method, AsyncMock())
    if regenerate:
        shots[0].image_url = "https://media.example.com/previous.png"
        shots[1].video_url = "/static/old.mp4"
        project.final_video_url = "/static/old-final.mp4"
        await pipeline.regen_shot_image(project.id, shots[1].id)
        calls = generate.await_args_list
        assert calls[0].args[2] == [shots[0].image_url, project.ref_image_url]
        assert shots[1].video_url is None and project.final_video_url is None
    else:
        await pipeline._parallel_image_and_audio(project.id)
        calls = generate.await_args_list
        assert len(calls) == 2
        assert calls[0].args[2] == [project.ref_image_url]
        assert calls[1].args[2] == ["https://media.example.com/static/generated/p991/shot_1.png", project.ref_image_url]
    for call in calls:
        assert call.kwargs["style_ref_urls"] == ["https://media.example.com/static/templates/covers/anim_3d.png"]
        assert call.kwargs["model"] == project.image_model
        assert "三维" in call.args[0] and "蓝色外套" in call.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_cap", [3, 2])
async def test_image_admission_reserves_style_subject_and_previous_slots_without_upload(monkeypatch, reference_cap):
    """图片引用数量在受理时核验，后镜必需的画风名额不能被忽略。"""
    template = SimpleNamespace(id="anim_3d", seedream_config={}, preview_cover="/static/style.png")
    shots = [SimpleNamespace(id=no, shot_no=no, image_url=None, image_ark_url=None) for no in (1, 2)]
    project = SimpleNamespace(id=1, template=template, ref_image_url="/static/subject.png", shots=shots,
                              image_model="seedream-5.0-pro", image_resolution="2K", output_ratio="9:16")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: project)))
    strategy = get_strategy("seedream-5.0-pro").with_reference_limits({"images": reference_cap, "videos": 0, "audios": 0})
    monkeypatch.setattr(planning, "resolve_strategy", lambda *_: strategy)
    monkeypatch.setattr(planning, "pin_route", lambda *_: {})
    monkeypatch.setattr(storage, "republish_url", lambda *_args, **_kwargs: pytest.fail("admission must not upload"))
    task = TaskRun(domain="kepu", task_type="project_pipeline", project_id=1, payload={"phase": "assets"})
    if reference_cap == 2:
        with pytest.raises(MediaValidationError, match="参考图片"):
            await planning.plan_items(db, task)
        return
    items = await planning.plan_items(db, task)
    assert items[0]["params"]["reference_images"] == ["/static/subject.png"]
    assert items[1]["params"]["reference_images"] == ["pending://shot-1-image", "/static/subject.png"]
    assert all(item["params"]["style_ref_urls"] == ["/static/style.png"] for item in items)
