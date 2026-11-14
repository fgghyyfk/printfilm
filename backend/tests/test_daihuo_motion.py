"""带货动态展示：动态镜头选择、首帧裁切、提示词、阶段判定、声音、估价、逐镜切换与视频请求体。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.models import Project, ProjectStatus, Shot, Template
from app.services.daihuo import motion, script
from app.services.kepu_stages import VIDEO_SKIP_REASON_STATIC, resolve_kepu_billing_phase, shot_video_ready
from app.services.templates_seed_daihuo import DAIHUO_TEMPLATE_ID


# 视频形式：静图无动态，全动态全部，混合首镜必选并按模型建议补到上限
def test_pick_motion_positions():
    assert motion.pick_motion_positions([True, True], "static") == set()
    assert motion.pick_motion_positions([False] * 4, "motion") == {0, 1, 2, 3}
    assert motion.pick_motion_positions([False, True, True, True, True], "mixed", max_shots=3) == {0, 1, 2}
    assert motion.pick_motion_positions([False, False, False, True], "mixed", max_shots=3) == {0, 3}
    # 模型一个都没建议：补第二镜，保证至少两处动态
    assert motion.pick_motion_positions([False] * 4, "mixed", max_shots=3) == {0, 1}
    assert motion.pick_motion_positions([False, False], "mixed", max_shots=3) == {0}
    assert motion.normalize_form("bogus") == "static"
    assert motion.pipeline_mode_for("mixed") == "full" and motion.pipeline_mode_for("static") == "image_text"


# 画幅接近（3:4→9:16 保留 75%）直接裁满幅；方图→竖屏切太多，交给扩图
def test_crop_full_bleed(tmp_path: Path):
    tall = tmp_path / "tall.jpg"
    Image.new("RGB", (1728, 2304), "navy").save(tall)
    square = tmp_path / "square.jpg"
    Image.new("RGB", (1920, 1920), "navy").save(square)
    dest = tmp_path / "daihuo" / "motion_01_x.jpg"
    assert motion.crop_full_bleed(tall, dest, ratio="9:16")
    assert Image.open(dest).size == (1080, 1920)
    assert not motion.crop_full_bleed(square, tmp_path / "daihuo" / "motion_02_x.jpg", ratio="9:16")
    assert motion.crop_keep_ratio((1080, 1920), "9:16") == pytest.approx(1.0)
    assert motion.is_motion_frame("/static/generated/p1/daihuo/motion_01_ab.jpg")
    assert not motion.is_motion_frame("/static/generated/p1/daihuo/frame_01_ab.jpg")
    # 用户重绘出的整幅新图可直接当首帧，不再扩图
    assert motion.is_motion_frame("/static/generated/p1/shot_03_new.png")
    assert not motion.is_motion_frame("")


# 提示词：动作描述 + 补充 + 保真约束；未知动作回落默认
def test_build_video_prompt_and_action_key():
    text = motion.build_video_prompt("orbit", "壶身反光自然")
    assert "环绕" in text and "壶身反光自然" in text and "logo" in text and "不新增任何文字" in text
    assert motion.action_key("unknown", first=True) == motion.DEFAULT_FIRST_ACTION
    assert motion.action_key("unknown", first=False) == motion.DEFAULT_ACTION
    assert motion.action_key("macro", first=True) == "macro"


# 写稿解析带出动作建议，补充描述同样做极限词替换
def test_parse_segments_reads_motion_fields():
    items = [{"idx": 0, "title": "保温", "subtitle": ""}, {"idx": 1, "title": "大容量", "subtitle": ""}]
    raw = (
        '{"title":"好壶","segments":['
        '{"idx":0,"title":"保温","narration":"这款保温壶早上装的热水晚上还烫嘴","motion":true,"action":"orbit","video":"最好的环绕展示"},'
        '{"idx":1,"title":"大容量","narration":"两点五升大容量一家人喝一天都够","motion":"false","action":"x"}]}'
    )
    out = script.parse_segments(raw, items, name="保温壶")
    first, second = out["segments"]
    # 动作补充只进视频提示词、不上屏，约束句已禁止画面出现文字
    assert first["motion"] is True and first["action"] == "orbit" and first["video"] == "最好的环绕展示"
    assert second["motion"] is False and second["video"] == ""
    assert "motion" in script.motion_addon() and "orbit" in script.motion_addon()


def _shot(**kw) -> Shot:
    base = dict(shot_no=1, duration=4.0, narration="口播", image_url="/static/generated/p1/daihuo/frame_01.jpg",
                video_url=None, audio_url=None, video_skip_reason=None)
    base.update(kw)
    return Shot(**base)


# 静图镜头算视频已就绪：不阻塞视频阶段，也不进视频估价
def test_static_shots_are_video_ready(monkeypatch):
    static = _shot(video_skip_reason=VIDEO_SKIP_REASON_STATIC)
    assert shot_video_ready(static)
    project = Project(pipeline_mode="full", template_id=DAIHUO_TEMPLATE_ID, shots=[static])
    monkeypatch.setattr("app.services.kepu_stages.project_audio_ready", lambda _p: True)
    assert resolve_kepu_billing_phase(project) == "compose"
    project.shots.append(_shot(shot_no=2))
    assert resolve_kepu_billing_phase(project) == "videos"


# 静图镜头改画面描述或动作描述后仍是静图（不被当成隐私结论清掉）
async def test_shot_edit_keeps_static_marker(db_session):
    from sqlalchemy import select

    from app.api.projects import update_shot
    from app.schemas import ShotUpdate

    user, project = await _seed_project(db_session)
    shot = (await db_session.execute(select(Shot).where(Shot.project_id == project.id, Shot.shot_no == 2))).scalar_one()
    await update_shot(project.id, shot.id, ShotUpdate(img_prompt="换一个场景"), db=db_session, user=user)
    await update_shot(project.id, shot.id, ShotUpdate(video_prompt="新的动作描述"), db=db_session, user=user)
    await db_session.refresh(shot)
    assert shot.video_skip_reason == VIDEO_SKIP_REASON_STATIC


# 带货 full 没配音时停在 assets，不能像科普内置口播那样直接进视频
def test_daihuo_full_requires_narration_before_videos():
    project = Project(pipeline_mode="full", template_id=DAIHUO_TEMPLATE_ID, shots=[_shot(), _shot(shot_no=2)])
    assert resolve_kepu_billing_phase(project) == "assets"


# 带货 full 不要 Seedance 内置口播/音效；普通科普 full 不受影响
def test_daihuo_full_uses_post_tts():
    from app.services import pipeline

    daihuo = Project(pipeline_mode="full", template_id=DAIHUO_TEMPLATE_ID)
    assert not pipeline._kepu_seedance_native_audio(daihuo)
    assert not pipeline._kepu_seedance_sfx_audio(daihuo)
    assert not pipeline._kepu_seedance_generate_audio(daihuo)
    kepu = Project(pipeline_mode="full", template_id="kepu_x")
    assert pipeline._kepu_seedance_native_audio(kepu) == bool(pipeline.get_settings().kepu_seedance_native_audio)


# 视频估价：只算动态镜头；没有满幅首帧的另加一张扩图（扩图不乘缓冲）
def test_videos_estimate_counts_motion_and_outpaint(monkeypatch):
    from app.services.billing import estimates

    monkeypatch.setattr(estimates, "charge_fen_official_video", lambda secs, *_a, **_k: int(secs * 10))
    monkeypatch.setattr(estimates, "_catalog_image_fen", lambda *_a, **_k: 7)
    monkeypatch.setattr(estimates, "_estimate_privacy_audio_fen", lambda *_a, **_k: 0)
    settings = SimpleNamespace(billing_estimate_buffer=1.0, ark_video_resolution="480p", model_video="m")
    monkeypatch.setattr(estimates, "_payload_video_model", lambda *_a: "m")
    shots = [
        _shot(shot_no=1, duration=4.0, image_url="/static/x/daihuo/motion_01.jpg"),
        _shot(shot_no=2, duration=4.0),
        _shot(shot_no=3, duration=4.0, video_skip_reason=VIDEO_SKIP_REASON_STATIC),
    ]
    project = Project(pipeline_mode="full", template_id=DAIHUO_TEMPLATE_ID, shots=shots, resolution_mode="preview")
    secs = motion.video_duration(4.0)
    assert estimates._estimate_videos_fen(project, settings) == 2 * secs * 10 + 7


# 视频请求参数：首帧角色、纯文本、可内嵌、不串尾帧；时长覆盖口播
def test_video_kwargs(monkeypatch):
    kw = motion.video_kwargs(image_url="/static/a.jpg", prompt="p", duration=6.2)
    assert kw["image_role"] == "first_frame" and kw["prompt_as_json"] is False
    assert kw["extra_image_urls"] is None and kw["duration"] == 7
    assert motion.video_duration(1.0) == motion.get_settings().seedance_duration_min
    assert motion.video_duration(30) == 12


async def _seed_project(db, *, mode="full", form="mixed"):
    from tests.conftest import make_user

    user = await make_user(db)
    if not await db.get(Template, DAIHUO_TEMPLATE_ID):
        db.add(Template(id=DAIHUO_TEMPLATE_ID, name="带货模板", style_prefix=""))
    project = Project(user_id=user.id, template_id=DAIHUO_TEMPLATE_ID, title="壶", source_type="script",
                      source_text="壶", pipeline_mode=mode, daihuo_video_form=form, status=ProjectStatus.DONE,
                      final_video_url="/static/final.mp4")
    db.add(project)
    await db.flush()
    for no in (1, 2, 3):
        db.add(Shot(project_id=project.id, shot_no=no, duration=4, narration="口播", img_prompt="",
                    video_prompt=motion.build_video_prompt("push_in") if no == 1 else "",
                    camera="缓慢推近" if no == 1 else "slow push in",
                    image_url=f"/static/generated/p{project.id}/daihuo/frame_0{no}.jpg",
                    video_url="/static/v.mp4" if no == 1 else None,
                    video_skip_reason=None if (no == 1 or mode != "full") else VIDEO_SKIP_REASON_STATIC))
    await db.commit()
    return user, project


# 逐镜切换：静图→动态清视频并改动作；动态→静图；成片作废、状态回退
async def test_set_shot_motion_toggles(db_session):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.services.daihuo.motion_edit import set_shot_motion

    user, project = await _seed_project(db_session)
    shots = sorted((await db_session.execute(select(Shot).where(Shot.project_id == project.id))).scalars(),
                   key=lambda s: s.shot_no)
    turned = await set_shot_motion(db_session, user, project.id, shots[1].id, motion=True, action="macro",
                                   detail="微距看清磨砂质感")
    assert turned.video_skip_reason is None and turned.camera == "微距扫过" and "磨砂" in turned.video_prompt
    off = await set_shot_motion(db_session, user, project.id, shots[0].id, motion=False)
    assert off.video_skip_reason == VIDEO_SKIP_REASON_STATIC and off.video_url is None
    fresh = (await db_session.execute(select(Project).where(Project.id == project.id)
                                      .options(selectinload(Project.shots))
                                      .execution_options(populate_existing=True))).scalar_one()
    # 种子镜头没有配音 → 回到配音前（SCRIPT_READY）
    assert fresh.final_video_url is None and fresh.status == ProjectStatus.SCRIPT_READY
    assert fresh.daihuo_video_form == "mixed"
    with pytest.raises(ValueError):
        await set_shot_motion(db_session, user, project.id, shots[0].id, motion=True, action="fly")


# 静图口播项目打开某镜动态：整体转混合，其余镜头标静图
async def test_set_shot_motion_upgrades_static_project(db_session):
    from sqlalchemy import select

    from app.services.daihuo.motion_edit import set_shot_motion

    user, project = await _seed_project(db_session, mode="image_text", form="static")
    shots = sorted((await db_session.execute(select(Shot).where(Shot.project_id == project.id))).scalars(),
                   key=lambda s: s.shot_no)
    await set_shot_motion(db_session, user, project.id, shots[2].id, motion=True)
    await db_session.refresh(project)
    for shot in shots:
        await db_session.refresh(shot)
    assert project.pipeline_mode == "full" and project.daihuo_video_form == "mixed"
    assert [s.video_skip_reason for s in shots] == [VIDEO_SKIP_REASON_STATIC, VIDEO_SKIP_REASON_STATIC, None]
    assert shots[2].video_prompt and shots[2].camera == "光影扫过"


# 生成中的项目不允许切换；别人的项目不可见
async def test_set_shot_motion_guards(db_session):
    from sqlalchemy import select

    from app.models_tasks import TaskRun
    from app.services.daihuo.motion_edit import set_shot_motion
    from tests.conftest import make_user

    user, project = await _seed_project(db_session)
    shot = (await db_session.execute(select(Shot).where(Shot.project_id == project.id))).scalars().first()
    stranger = await make_user(db_session)
    with pytest.raises(LookupError):
        await set_shot_motion(db_session, stranger, project.id, shot.id, motion=False)
    db_session.add(TaskRun(requested_by=user.id, domain="kepu", task_type="project_pipeline", project_id=project.id,
                           status="running", payload={}))
    await db_session.commit()
    with pytest.raises(ValueError, match="生成中"):
        await set_shot_motion(db_session, user, project.id, shot.id, motion=False)


# 未开 OSS 且禁止内嵌时动态展示不可用，建项目被拒
def test_motion_unavailable_reason(monkeypatch):
    fake = SimpleNamespace(ark_mock=False, oss_enabled=False, daihuo_video_inline_refs=False)
    monkeypatch.setattr(motion, "get_settings", lambda: fake)
    assert "OSS" in (motion.motion_unavailable_reason() or "")
    fake.daihuo_video_inline_refs = True
    assert motion.motion_unavailable_reason() is None


@pytest.fixture
def isolate_routing():
    """保存渠道会改进程级路由缓存，用例结束后还原，避免污染其他用例。"""
    from app.services import model_settings

    snapshot = model_settings._routing_snapshot
    overlay = dict(model_settings._overlay)
    yield
    model_settings._routing_snapshot = snapshot
    model_settings._overlay.clear()
    model_settings._overlay.update(overlay)
    model_settings.reload_settings()


# 请求体：带货首帧不传画幅、角色 first_frame；无公网地址时本地图内嵌为 data URI
async def test_gen_video_i2v_first_frame_inline(db_session, monkeypatch, tmp_path, isolate_routing):
    import json
    from unittest.mock import AsyncMock

    import httpx

    from app.schemas_routing import AdminRoutingSettingsPatch
    from app.services import model_settings, seedance_image_aspect, storage
    from app.services.ark import ArkGateway
    from tests.test_provider_profiles import AP, profiles

    items = profiles()
    items[1]["advanced_config"]["video_model"] = "dreamina-seedance-2-5-260628"
    await model_settings.patch_admin_routing_settings(db_session, AdminRoutingSettingsPatch(system_channels=items))
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        assert str(req.url) == AP + "/contents/generations/tasks"
        return httpx.Response(200, json={"id": "task-1"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)))
    frame = tmp_path / "motion_01.jpg"
    Image.new("RGB", (1080, 1920), "navy").save(frame)
    monkeypatch.setattr(seedance_image_aspect, "ensure_seedance_compatible_image_url", AsyncMock(side_effect=lambda u: u))
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: frame if url == "/static/f.jpg" else None)
    gw = ArkGateway()
    monkeypatch.setattr(gw, "_resolve_image_ref", AsyncMock(side_effect=RuntimeError("Seedance 需要公网可访问的图片 URL")))
    await gw.gen_video_i2v("/static/f.jpg", "商品展示镜头", 5, ratio="9:16", image_role="first_frame",
                           inline_refs=True, prompt_as_json=False)
    body = calls[0]
    image = next(c for c in body["content"] if c["type"] == "image_url")
    assert image["role"] == "first_frame" and image["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "ratio" not in body and body["generate_audio"] is False
    text = next(c for c in body["content"] if c["type"] == "text")["text"]
    assert text == "商品展示镜头"
    # 不允许内嵌时保持原报错
    with pytest.raises(RuntimeError):
        await gw.gen_video_i2v("/static/f.jpg", "x", 5, ratio="9:16", image_role="first_frame")


# 报价：混合展示按上限估动态镜头，分项给出扩图与视频；静图口播不变
async def test_quote_mixed_adds_video_and_outpaint(db_session, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.daihuo import service
    from tests.test_daihuo_project_options import seed_set

    user, ecom = await seed_set(db_session, tmp_path)
    monkeypatch.setattr("app.services.billing.settlement.billing_active", lambda *_: True)
    monkeypatch.setattr("app.services.tokenfree_pricing.ensure_official_rates", AsyncMock())
    monkeypatch.setattr("app.services.billing.estimates._catalog_image_fen", lambda *_a, **_k: 9)

    def estimate(project, phase, settings):
        if phase == "videos":
            return 100 + 9 * sum(1 for s in project.shots if not motion.is_motion_frame(s.image_url))
        return 10

    monkeypatch.setattr("app.services.billing.estimates.estimate_phase_fen", estimate)
    static = await service.quote_project_from_set(db_session, user, ecom.id, output_ratio="9:16")
    assert static["video_form"] == "static" and static["video_fen"] == 0 and static["motion_shots"] == 0
    mixed = await service.quote_project_from_set(db_session, user, ecom.id, output_ratio="9:16", video_form="mixed")
    # 种子图不是真图片 → 视为需扩图；两张可用图都进动态
    assert mixed["motion_shots"] == 2 and mixed["image_fen"] == 18 and mixed["video_fen"] == 100
    assert mixed["estimate_fen"] == 10 + 10 + 100 + 18 and mixed["motion_available"] is True


# 建项目：混合展示走 full 并记录形式；同请求键换形式视为冲突
async def test_create_project_records_form_and_rejects_conflict(db_session, tmp_path):
    from app.services.daihuo import service
    from tests.test_daihuo_project_options import seed_set

    user, ecom = await seed_set(db_session, tmp_path)
    project = await service.create_project_from_set(db_session, user, ecom.id, video_form="mixed",
                                                   create_new=True, request_id="motion-request-0001")
    assert project.pipeline_mode == "full" and project.daihuo_video_form == "mixed"
    with pytest.raises(ValueError):
        await service.create_project_from_set(db_session, user, ecom.id, video_form="motion",
                                              create_new=True, request_id="motion-request-0001")
