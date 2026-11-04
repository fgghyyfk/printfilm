# -*- coding: utf-8 -*-
"""PATCH 分镜：改旁白作废整片连贯音轨，任何编辑都要把终态打回对应阶段。"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import update_shot
from app.config import get_settings
from app.models import Project, ProjectStatus, Shot, ShotStatus, Template
from app.schemas import ShotUpdate
from app.services.seedance_segments import apply_segment_script_edit, replace_narration_in_script

from tests.conftest import make_user


async def _make_done_project(db: AsyncSession, user, tmp_path: Path) -> tuple[Project, Shot, Shot]:
    """构造 DONE 项目：两镜均有图/视频/配音，整片连贯音轨文件存在。"""
    tpl = Template(id="tpl-demote-1", name="作废测试模板", style_prefix="x")
    db.add(tpl)
    await db.flush()
    project = Project(
        user_id=user.id,
        template_id=tpl.id,
        source_text="测试",
        status=ProjectStatus.DONE,
        final_video_url="/static/final.mp4",
    )
    db.add(project)
    await db.flush()
    shots = []
    for no in (1, 2):
        # 与编辑弹窗一致：脚本、旁白及提示词保持同步。
        fields = apply_segment_script_edit(
            f"@duration:4\n旧图提示{no}\n@duration:4\n【旁白·自然语速·同步字幕】旧旁白{no}",
            bgm_mood="neutral",
        )
        shot = Shot(
            project_id=project.id,
            shot_no=no,
            **fields,
            image_url=f"/static/p{project.id}/old_{no}.png",
            image_ark_url=f"http://up/old_{no}.png",
            video_url=f"/static/p{project.id}/v_{no}.mp4",
            last_frame_url=f"/static/p{project.id}/last_{no}.png",
            audio_url=f"/static/p{project.id}/full_narration.mp3",
            status=ShotStatus.VIDEO_READY,
        )
        db.add(shot)
        shots.append(shot)
    await db.flush()
    narration = tmp_path / "full_narration.mp3"
    narration.write_bytes(b"x" * 3000)
    return project, shots[0], shots[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("native_audio", [False, True])
@pytest.mark.parametrize("full_payload", [False, True])
@pytest.mark.parametrize("legacy_script", [False, True])
async def test_narration_edit_invalidates_continuous_audio(
    db_session: AsyncSession, monkeypatch, tmp_path, native_audio, full_payload, legacy_script
) -> None:
    """旁白真实改变后失效对应制作输入，native 模式不清其他镜头备用音轨。"""
    user = await make_user(db_session)
    project, shot1, shot2 = await _make_done_project(db_session, user, tmp_path)
    if legacy_script:
        shot1.segment_script = ""
        shot1.video_prompt = "画面轻微动态"
    monkeypatch.setattr(get_settings(), "kepu_seedance_native_audio", native_audio)
    # 提交全字段时，后端还会将旁白同步回脚本和视频提示词。
    fields = _full_edit_payload(shot1) if full_payload else {}
    fields["narration"] = "这是全新的旁白内容"
    media = (shot1.image_url, shot1.image_ark_url, shot1.video_url, shot1.last_frame_url)
    monkeypatch.setattr(
        "app.services.storage.project_dir", lambda _pid: tmp_path
    )

    await db_session.flush()  # 模拟请求开始前已持久化的历史项目/镜头。
    await update_shot(
        project.id,
        shot1.id,
        ShotUpdate(**fields),
        db=db_session,
        user=user,
    )

    assert not (tmp_path / "full_narration.mp3").exists()
    await db_session.refresh(shot1)
    await db_session.refresh(shot2)
    assert shot1.audio_url is None
    assert shot2.audio_url == (media[0].rsplit("/", 1)[0] + "/full_narration.mp3" if native_audio else None)
    assert (shot1.image_url, shot1.image_ark_url) == media[:2]
    assert (shot1.video_url, shot1.last_frame_url) == ((None, None) if native_audio else media[2:])
    if not legacy_script:
        assert "这是全新的旁白内容" in shot1.segment_script
    await db_session.refresh(project)
    assert project.final_video_url is None
    # 内置口播需重做视频；外部配音需重做音频素材。
    assert project.status == (ProjectStatus.IMAGE_READY if native_audio else ProjectStatus.SCRIPT_READY)


def _full_edit_payload(shot: Shot) -> dict:
    """模拟前端保存时始终携带的全部分镜字段。"""
    return {key: getattr(shot, key) for key in (
        "narration", "overlay_title", "overlay_subtitle", "img_prompt",
        "video_prompt", "segment_script", "duration", "camera",
    )}


@pytest.mark.asyncio
@pytest.mark.parametrize("edited_field", [None, "overlay_title", "overlay_subtitle"])
@pytest.mark.parametrize("legacy_script", [False, True])
async def test_full_payload_preserves_existing_media(
    db_session: AsyncSession, monkeypatch, tmp_path, edited_field, legacy_script
) -> None:
    """同值保存或只改叠字时，不作废画面、视频、尾帧和连贯音轨。"""
    user = await make_user(db_session)
    project, shot, _ = await _make_done_project(db_session, user, tmp_path)
    if legacy_script:
        shot.segment_script = ""
        shot.video_prompt = "画面轻微动态"
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    fields = _full_edit_payload(shot)
    if edited_field:
        fields[edited_field] = "新的叠字"
    media = (shot.image_url, shot.image_ark_url, shot.video_url, shot.last_frame_url, shot.audio_url)

    await db_session.flush()  # 模拟请求开始前已持久化的历史项目/镜头。
    await update_shot(project.id, shot.id, ShotUpdate(**fields), db=db_session, user=user)

    assert (shot.image_url, shot.image_ark_url, shot.video_url, shot.last_frame_url, shot.audio_url) == media
    assert (tmp_path / "full_narration.mp3").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("native_audio", [False, True])
@pytest.mark.parametrize("independent_image", [False, True])
async def test_frontend_narration_sync_respects_native_audio(
    db_session: AsyncSession, monkeypatch, tmp_path, native_audio, independent_image
) -> None:
    """前端从历史 video_prompt 回填脚本时，依实际口播模式失效视频并保留图。"""
    user = await make_user(db_session)
    project, shot, sibling = await _make_done_project(db_session, user, tmp_path)
    shot.segment_script = ""
    # 历史首帧描述独立于视频动作，旁白保存不能用首帧覆盖动作或反向回填首帧。
    if independent_image:
        shot.img_prompt = "主体静立的首帧"
    monkeypatch.setattr(get_settings(), "kepu_seedance_native_audio", native_audio)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    # 对应 patchEditingNarration：从实际使用的脚本回填两个制作字段。
    fields = _full_edit_payload(shot)
    fields["narration"] = "全新的旁白"
    script = replace_narration_in_script(shot.segment_script or shot.video_prompt, fields["narration"])
    fields.update(segment_script=script, video_prompt=script)
    media = (shot.image_url, shot.image_ark_url, shot.video_url, shot.last_frame_url)

    await db_session.flush()  # 模拟请求开始前已持久化的历史项目/镜头。
    await update_shot(project.id, shot.id, ShotUpdate(**fields), db=db_session, user=user)

    assert (shot.image_url, shot.image_ark_url) == media[:2]
    assert (shot.video_url, shot.last_frame_url) == ((None, None) if native_audio else media[2:])
    if independent_image:
        assert shot.img_prompt == "主体静立的首帧"
        assert "主体静立的首帧" not in shot.segment_script
    assert not (tmp_path / "full_narration.mp3").exists()
    assert shot.audio_url is None
    assert sibling.audio_url == (media[0].rsplit("/", 1)[0] + "/full_narration.mp3" if native_audio else None)
    assert project.status == (ProjectStatus.IMAGE_READY if native_audio else ProjectStatus.SCRIPT_READY)


@pytest.mark.asyncio
async def test_frontend_visual_sync_updates_script_and_invalidates_media(
    db_session: AsyncSession, monkeypatch, tmp_path
) -> None:
    """用户真实改首帧时仍同步制作脚本，并作废图和下游媒体。"""
    user = await make_user(db_session)
    project, shot, _ = await _make_done_project(db_session, user, tmp_path)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    fields = _full_edit_payload(shot)
    fields["img_prompt"] = "主体转身后的全新首帧"

    await update_shot(project.id, shot.id, ShotUpdate(**fields), db=db_session, user=user)

    assert "主体转身后的全新首帧" in shot.segment_script
    assert shot.image_url is None
    assert shot.image_ark_url is None
    assert shot.video_url is None
    assert shot.last_frame_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["video_prompt", "segment_script", "duration", "camera"])
@pytest.mark.parametrize("changed", [False, True])
async def test_video_fields_invalidate_only_on_change(
    db_session: AsyncSession, monkeypatch, tmp_path, field, changed
) -> None:
    """视频动作、时间轴、时长或机位真实变化才清视频，分镜图始终保留。"""
    user = await make_user(db_session)
    project, shot, _ = await _make_done_project(db_session, user, tmp_path)
    # video_prompt 仅在没有逐段脚本时作为流水线输入。
    if field == "video_prompt":
        shot.segment_script = ""
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    before = (shot.image_url, shot.image_ark_url, shot.video_url, shot.last_frame_url)
    value = getattr(shot, field)
    if changed:
        value = {"duration": 6.0, "camera": "zoom in"}.get(field, f"{value}\n镜头推进到主体特写")

    await db_session.flush()  # 模拟请求开始前已持久化的历史项目/镜头。
    await update_shot(project.id, shot.id, ShotUpdate(**{field: value}), db=db_session, user=user)

    assert (shot.image_url, shot.image_ark_url) == before[:2]
    assert (shot.video_url, shot.last_frame_url) == ((None, None) if changed else before[2:])


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [None, "narration", "img_prompt"])
async def test_avatar_edit_keeps_existing_invalidation_rules(
    db_session: AsyncSession, monkeypatch, tmp_path, field
) -> None:
    """数字人沿用场景图占位和逐段配音/对口型失效规则。"""
    user = await make_user(db_session)
    project, shot, _ = await _make_done_project(db_session, user, tmp_path)
    project.pipeline_mode = "avatar"
    project.avatar_image_url = "/static/avatar.png"
    shot.avatar_job_id = "old-avatar-job"
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    before = (shot.image_url, shot.video_url, shot.audio_url, shot.avatar_job_id)
    fields = {key: getattr(shot, key) for key in ("narration", "img_prompt", "overlay_title")}
    if field:
        fields[field] = "更新后的文字"

    await db_session.flush()  # 模拟请求开始前已持久化的历史项目/镜头。
    await update_shot(project.id, shot.id, ShotUpdate(**fields), db=db_session, user=user)

    assert shot.image_url == (project.avatar_image_url if field == "img_prompt" else before[0])
    assert shot.video_url == (None if field else before[1])
    assert shot.audio_url == (None if field == "narration" else before[2])
    assert shot.avatar_job_id == (None if field else before[3])
    assert (tmp_path / "full_narration.mp3").exists()


@pytest.mark.asyncio
async def test_img_prompt_edit_demotes_done_project(db_session: AsyncSession) -> None:
    """改首帧提示词：清下游素材，DONE 项目必须回退而非保留完成态。"""
    user = await make_user(db_session)
    tpl = Template(id="tpl-demote-2", name="作图废测试模板", style_prefix="x")
    db_session.add(tpl)
    await db_session.flush()
    project = Project(
        user_id=user.id,
        template_id=tpl.id,
        source_text="测试",
        status=ProjectStatus.DONE,
        final_video_url="/static/final2.mp4",
    )
    db_session.add(project)
    await db_session.flush()
    shot = Shot(
        project_id=project.id,
        shot_no=1,
        img_prompt="旧图提示",
        image_url="/static/old.png",
        image_ark_url="https://up/old.png",
        video_url="/static/old.mp4",
        last_frame_url="/static/old-last.png",
        video_skip_reason="privacy",
        status=ShotStatus.VIDEO_READY,
    )
    db_session.add(shot)
    await db_session.flush()

    await update_shot(
        project.id,
        shot.id,
        ShotUpdate(img_prompt="全新的画面要求"),
        db=db_session,
        user=user,
    )

    await db_session.refresh(shot)
    assert shot.image_url is None
    assert shot.image_ark_url is None
    assert shot.video_url is None
    assert shot.last_frame_url is None
    assert shot.video_skip_reason is None
    assert shot.status == ShotStatus.PENDING
    await db_session.refresh(project)
    assert project.final_video_url is None
    assert project.status != ProjectStatus.DONE


async def test_narration_only_patch_updates_legacy_spoken_video_prompt(db_session, monkeypatch, tmp_path):
    user = await make_user(db_session)
    project, shot, _ = await _make_done_project(db_session, user, tmp_path)
    shot.video_prompt, shot.segment_script = shot.segment_script, ""
    await db_session.commit()
    monkeypatch.setattr(get_settings(), "kepu_seedance_native_audio", True)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _: tmp_path)
    await update_shot(project.id, shot.id, ShotUpdate(narration="历史输入也同步新旁白"), db_session, user)
    assert shot.segment_script == ""
    assert "历史输入也同步新旁白" in shot.video_prompt
    assert shot.video_url is None
