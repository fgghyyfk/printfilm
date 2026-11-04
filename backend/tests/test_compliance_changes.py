"""生成文案审计：码点区间、原稿复核与逐条安全撤销。"""
from __future__ import annotations

import pytest

from app.services.compliance.ad_words import check_text


@pytest.mark.parametrize("text", ["续航最多12小时", "第一印象", "治疗仪配件", "最低温零下20度"])
def test_context_is_preserved(text):
    """普通搭配不改坏句意。"""
    assert check_text(text)[0] == text


def test_changes_keep_original_and_support_repeated_undo():
    """emoji、重复词和长度变化后的连续撤销仍定位同一处。"""
    from app.services.compliance.changes import clean_fields, undo_change

    original = "😀最好用的暖壶，顶级设计，顶级配置"
    cleaned, audit = clean_fields({"narration": original})
    assert cleaned["narration"] == "😀好用的暖壶，高端设计，高端配置"
    record = audit["narration"]
    assert record["original"] == original
    changes = record["changes"]
    assert len(changes) == 3
    assert changes[0]["old"] == "最好用"
    assert changes[0]["new"] == "好用"
    assert changes[0]["original_start"] == 1
    assert original[changes[0]["original_start"]:changes[0]["original_end"]] == changes[0]["old"]
    for change in changes:
        assert cleaned["narration"][change["start"]:change["end"]] == change["new"]
    current, audit = undo_change(cleaned, audit, "narration", changes[1]["id"])
    assert current["narration"] == "😀好用的暖壶，顶级设计，高端配置"
    current, audit = undo_change(current, audit, "narration", changes[0]["id"])
    assert current["narration"] == "😀最好用的暖壶，顶级设计，高端配置"
    current, audit = undo_change(current, audit, "narration", changes[2]["id"])
    assert current["narration"] == original
    assert audit["narration"]["original"] == original


def test_cleanup_is_recorded_and_manual_edits_block_undo():
    """标点清理也有记录；后续手改不按旧位置盲撤。"""
    from app.services.compliance.changes import clean_fields, undo_change

    original = "  绝对，，好用  "
    cleaned, audit = clean_fields({"narration": original})
    assert cleaned["narration"] == "  好用"
    current = cleaned
    for change in audit["narration"]["changes"]:
        current, audit = undo_change(current, audit, "narration", change["id"])
    assert current["narration"] == original
    cleaned, audit = clean_fields({"narration": "顶级设计"})
    with pytest.raises(ValueError, match="已编辑"):
        undo_change({"narration": "我手动保留的高端设计"}, audit, "narration", audit["narration"]["changes"][0]["id"])


async def _project_with_changes(db, tmp_path):
    """创建带真实制作字段的广告项目，审计入库后再执行API。"""
    from app.models import Project, Shot, Template
    from app.services.compliance.changes import clean_fields
    from tests.conftest import make_user

    user = await make_user(db)
    tpl = Template(id="audit-ad", name="带货审计", category=["带货"], style_prefix="x")
    db.add(tpl)
    await db.flush()
    project = Project(user_id=user.id, template_id=tpl.id, source_text="测试", title="生成标题", pipeline_mode="image_text", status="DONE", final_video_url="/static/final.mp4")
    db.add(project)
    await db.flush()
    fields, audit = clean_fields({"narration": "😀最好用的暖壶，顶级设计", "segment_script": "@duration:4\n画面：暖壶\n【旁白·自然语速·同步字幕】😀最好用的暖壶，顶级设计", "video_prompt": "@duration:4\n画面：暖壶\n【旁白·自然语速·同步字幕】😀最好用的暖壶，顶级设计"})
    shot = Shot(project_id=project.id, shot_no=1, **fields, compliance_changes=audit, image_url="/static/image.png", video_url="/static/video.mp4", audio_url="/static/audio.mp3", status="VIDEO_READY")
    db.add(shot)
    await db.commit()
    return user, project, shot


async def test_manual_edits_only_report_and_keep_generation_original(db_session, tmp_path, monkeypatch):
    """用户保留极限词时不再静默替换，扫描不误报为已处理。"""
    from app.api.projects import update_shot, project_compliance
    from app.schemas import ShotUpdate

    user, project, shot = await _project_with_changes(db_session, tmp_path)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _id: tmp_path)
    await update_shot(project.id, shot.id, ShotUpdate(narration="这是顶级设计"), db=db_session, user=user)
    await db_session.refresh(shot)
    assert shot.narration == "这是顶级设计"
    assert shot.compliance_changes["narration"]["original"] == "😀最好用的暖壶，顶级设计"
    report = await project_compliance(project.id, db=db_session, user=user)
    assert report["mode"] == "report" and report["auto_replace"] is False
    assert report["count"] > 0
    assert report["replacements"][0]["matches_current"] is False


async def test_history_survives_reload_and_undo_uses_existing_media_rules(db_session, tmp_path, monkeypatch):
    """无当前命中仍返回历史，撤销同步脚本并作废真实音轨/成片。"""
    from app.api.projects import update_shot, project_compliance
    from app.schemas import ShotUpdate, ShotOut

    user, project, shot = await _project_with_changes(db_session, tmp_path)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _id: tmp_path)
    (tmp_path / "full_narration.mp3").write_bytes(b"old")
    report = await project_compliance(project.id, db=db_session, user=user)
    assert report["count"] == 0
    assert report["replacements"]
    assert ShotOut.model_validate(shot).compliance_changes["narration"]["original"].startswith("😀")
    original_image = shot.image_url
    changes = shot.compliance_changes["narration"]["changes"]
    for change in changes:
        await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": "narration", "change_id": change["id"]}), db=db_session, user=user)
    assert shot.narration == "😀最好用的暖壶，顶级设计"
    assert shot.narration in shot.segment_script and shot.narration in shot.video_prompt
    assert shot.image_url == original_image
    assert shot.audio_url is None
    assert not (tmp_path / "full_narration.mp3").exists()
    assert project.final_video_url is None
    assert all(c["undone"] for c in shot.compliance_changes["narration"]["changes"])


async def test_undo_rejects_edited_text_and_project_title_undo_persists(db_session, tmp_path, monkeypatch):
    """旧记录不能覆盖手改，项目标题按相同原稿/跨度撤销。"""
    from fastapi import HTTPException
    from app.api.projects import update_shot, update_project
    from app.schemas import ShotUpdate, ProjectUpdate, ProjectOut
    from app.services.compliance.changes import clean_fields

    user, project, shot = await _project_with_changes(db_session, tmp_path)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _id: tmp_path)
    await update_shot(project.id, shot.id, ShotUpdate(narration="手动改过的高端设计"), db=db_session, user=user)
    with pytest.raises(HTTPException) as exc:
        await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": "narration", "change_id": "0"}), db=db_session, user=user)
    assert exc.value.status_code == 409
    fields, audit = clean_fields({"title": "😀顶级好物"})
    project.title, project.compliance_changes = fields["title"], audit
    await db_session.commit()
    await update_project(project.id, ProjectUpdate(compliance_undo={"field": "title", "change_id": "0"}), db=db_session, user=user)
    await db_session.refresh(project)
    assert project.title == "😀顶级好物"
    await db_session.refresh(project, ["shots"])
    assert ProjectOut.model_validate(project).compliance_changes["title"]["original"] == "😀顶级好物"


@pytest.mark.parametrize("reverse", [False, True])
def test_every_block_word_reconstructs_and_undoes_losslessly(reverse):
    """整张现有词表的真实结果可重建，emoji和重复词不丢插入或删除字符。"""
    from app.services.compliance.ad_words import BLOCK_WORDS
    from app.services.compliance.changes import clean_fields, render_record, undo_change

    for pairs in BLOCK_WORDS.values():
        for word, _ in pairs:
            for original in (word, f"😀{word}，{word}末尾", f"😀{word}{word}末尾"):
                cleaned, audit = clean_fields({"narration": original})
                if not audit:
                    continue
                record = audit["narration"]
                assert render_record(record)[0] == cleaned["narration"], original
                current = cleaned
                changes = list(record["changes"])
                for change in reversed(changes) if reverse else changes:
                    current, audit = undo_change(current, audit, "narration", change["id"])
                assert current["narration"] == original


async def test_script_undo_updates_linked_narration_and_next_undo_stays_valid(db_session, tmp_path, monkeypatch):
    """从脚本入口撤销仍同步旁白，并能继续从旁白撤销另一条。"""
    from app.api.projects import update_shot
    from app.schemas import ShotUpdate

    user, project, shot = await _project_with_changes(db_session, tmp_path)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _id: tmp_path)
    await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": "segment_script", "change_id": "1"}), db=db_session, user=user)
    assert shot.narration == "😀好用的暖壶，顶级设计"
    assert shot.narration in shot.segment_script and shot.narration in shot.video_prompt
    await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": "narration", "change_id": "0"}), db=db_session, user=user)
    assert shot.narration == "😀最好用的暖壶，顶级设计"
    assert shot.narration in shot.segment_script and shot.narration in shot.video_prompt


async def test_manual_visible_scripts_and_second_title_are_reported(db_session, tmp_path, monkeypatch):
    """只扫描脚本旁白及可见标题，不扫描画面描述，也不改用户文字。"""
    from app.api.projects import project_compliance

    user, project, shot = await _project_with_changes(db_session, tmp_path)
    project.avatar_title2 = "顶级好物"
    shot.narration = "普通旁白"
    shot.segment_script = "@duration:5\n画面：国家级建筑\n【旁白·自然语速】顶级设计"
    shot.video_prompt = shot.segment_script
    await db_session.commit()
    report = await project_compliance(project.id, db=db_session, user=user)
    fields = {item["field"] for item in report["items"]}
    assert {"avatar_title2", "segment_script", "video_prompt"} <= fields
    assert not any(hit["word"] == "国家级" for item in report["items"] for hit in item["hits"])
    assert shot.segment_script.endswith("顶级设计")


async def test_undo_refuses_active_generation_before_changing_audit(db_session, tmp_path):
    """生成期间不改变文本或审计状态。"""
    from copy import deepcopy
    from fastapi import HTTPException
    from app.api.projects import update_shot
    from app.schemas import ShotUpdate

    user, project, shot = await _project_with_changes(db_session, tmp_path)
    from tests.conftest import make_task

    task = await make_task(db_session, user, domain="kepu", task_type="project_pipeline", status="running")
    task.project_id = project.id
    await db_session.commit()
    before = deepcopy(shot.compliance_changes)
    with pytest.raises(HTTPException) as exc:
        await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": "narration", "change_id": "0"}), db=db_session, user=user)
    assert exc.value.status_code == 409
    assert shot.compliance_changes == before


async def test_multiline_spoken_undo_changes_only_matching_script_line(db_session, tmp_path, monkeypatch):
    """一镜多段旁白撤销一词时，保留分段与画面，不把整镜旁白塞入第一段。"""
    from app.api.projects import update_shot
    from app.schemas import ShotUpdate
    from app.services.compliance.changes import clean_fields
    from app.services.seedance_segments import narration_from_script

    user, project, shot = await _project_with_changes(db_session, tmp_path)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _id: tmp_path)
    script = "@duration:3\n画面：暖壶\n【旁白·自然语速】😀最好用的暖壶。\n@duration:3\n画面：细节\n【旁白·自然语速】顶级设计"
    fields, audit = clean_fields({"narration": "😀最好用的暖壶。顶级设计", "segment_script": script, "video_prompt": script})
    for key, value in fields.items():
        setattr(shot, key, value)
    shot.compliance_changes = audit
    await db_session.commit()
    await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": "narration", "change_id": "1"}), db=db_session, user=user)
    assert narration_from_script(shot.segment_script) == shot.narration == "😀好用的暖壶。顶级设计"
    assert shot.segment_script.count("【旁白") == 2
    assert "【旁白·自然语速】😀好用的暖壶。\n@duration" in shot.segment_script
    await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": "video_prompt", "change_id": "0"}), db=db_session, user=user)
    assert narration_from_script(shot.segment_script) == shot.narration == "😀最好用的暖壶。顶级设计"


@pytest.mark.parametrize("fields", [
    {"narration": "😀最新最强最优，最好用全网第一100%纯\n  绝对，，顶级  "},
    {"narration": "续航最多12小时，最低温20度，第一印象，治疗仪配件，顶级好物"},
    {"narration": "😀NO.1 最轻薄，1000%抑菌，TOP1"},
    {"narration": "高端设计", "highlights": "顶级设计|高端设计|别的"},
    {"segment_script": "@duration:5\r\n画面：顶级陈设\r\n【旁白·自然语速】😀最新，最强\r\n【旁白】全网最好用，\r\n【BGM】顶级轻快"},
])
def test_mixed_fields_reconstruct_and_restore_exact_original(fields):
    """混合词、正则、白名单、关键词和CRLF仍保存完整可逆原稿。"""
    from app.services.compliance.changes import clean_fields, render_record, undo_change

    cleaned, audit = clean_fields(fields)
    for field, record in audit.items():
        assert render_record(record)[0] == cleaned[field]
        current = cleaned
        updated = audit
        for change in reversed(record["changes"]):
            current, updated = undo_change(current, updated, field, change["id"])
        assert current[field] == fields[field]


@pytest.mark.parametrize("field", ["title", "avatar_title2"])
@pytest.mark.parametrize("is_undo", [True, False])
async def test_avatar_title_undo_invalidates_composed_video_only(db_session, tmp_path, monkeypatch, field, is_undo):
    """烧录标题撤销只作废成片引用，保留素材/文件；普通手改沿用既有策略。"""
    from app.api.projects import update_project
    from app.schemas import ProjectUpdate
    from app.services.compliance.changes import clean_fields

    user, project, shot = await _project_with_changes(db_session, tmp_path)
    fields, audit = clean_fields({field: "😀顶级好物"})
    setattr(project, field, fields[field])
    project.compliance_changes = audit
    project.pipeline_mode = "avatar"
    project.status = "DONE"
    final_file = tmp_path / "final.mp4"
    final_file.write_bytes(b"existing composed video")
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"existing shot audio")
    monkeypatch.setattr("app.services.storage.local_path_from_url", lambda _url: audio_file)
    monkeypatch.setattr("app.services.kepu_stages.is_near_silent_audio", lambda _path: False)
    media = shot.image_url, shot.video_url, shot.audio_url
    final_url = project.final_video_url
    await db_session.commit()

    body = ProjectUpdate(compliance_undo={"field": field, "change_id": "0"}) if is_undo else ProjectUpdate(**{field: "手动标题"})
    result = await update_project(project.id, body, db=db_session, user=user)

    assert getattr(result, field) == ("😀顶级好物" if is_undo else "手动标题")
    assert result.final_video_url == (None if is_undo else final_url)
    assert result.status == ("VIDEO_READY" if is_undo else "DONE")
    assert (shot.image_url, shot.video_url, shot.audio_url) == media
    assert final_file.read_bytes() == b"existing composed video"
    assert audio_file.read_bytes() == b"existing shot audio"
    assert result.compliance_changes[field]["original"] == "😀顶级好物"
    assert result.compliance_changes[field]["changes"][0]["undone"] is is_undo


@pytest.mark.parametrize("first_field", ["narration", "segment_script", "video_prompt"])
async def test_undo_maps_spoken_text_before_duplicate_visual_text(db_session, tmp_path, monkeypatch, first_field):
    """画面描述与口播重复时，仅撤销真实口播，并保持连续/反向撤销的关联审计。"""
    from app.api.projects import update_shot
    from app.schemas import ShotUpdate
    from app.services.compliance.changes import clean_fields, change_rows

    user, project, shot = await _project_with_changes(db_session, tmp_path)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _id: tmp_path)
    visual = "画面展示最新，最强的字样"
    script = f"{visual}\n【旁白】最新，最强"
    fields, audit = clean_fields({"narration": "最新，最强", "segment_script": script, "video_prompt": script})
    for key, value in fields.items():
        setattr(shot, key, value)
    shot.compliance_changes = audit
    await db_session.commit()

    await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": first_field, "change_id": "0"}), db=db_session, user=user)
    assert shot.narration == "最新，强劲"
    assert shot.segment_script == shot.video_prompt == f"{visual}\n【旁白】最新，强劲"
    for row in change_rows(shot, scope="shot"):
        assert row["matches_current"] is True
        assert row["changes"][0]["undone"] is True
        assert row["changes"][1]["undone"] is False

    second_field = "segment_script" if first_field == "narration" else "narration"
    await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": second_field, "change_id": "1"}), db=db_session, user=user)
    assert shot.narration == "最新，最强"
    assert shot.segment_script == shot.video_prompt == script
    for row in change_rows(shot, scope="shot"):
        assert row["matches_current"] is True
        assert all(change["undone"] for change in row["changes"])


@pytest.mark.parametrize("script", [
    "@duration:3\n画面：暖壶\n【旁白】顶级材质。\n@duration:3\n画面：暖壶\n【旁白】顶级设计，，多款可选。",
    "【旁白】顶级\n【旁白】顶级，，多款。",
])
async def test_multiline_punctuation_undo_keeps_script_beats_and_audit_in_sync(db_session, tmp_path, monkeypatch, script):
    """重复标点在整镜/逐行清理中区间不同，撤销仍只改目标口播段而不重复旁白。"""
    from app.api.projects import update_shot
    from app.schemas import ShotUpdate
    from app.services.compliance.changes import clean_fields, change_rows
    from app.services.seedance_segments import narration_from_script

    user, project, shot = await _project_with_changes(db_session, tmp_path)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _id: tmp_path)
    original_narration = narration_from_script(script)
    fields, audit = clean_fields({"narration": original_narration, "segment_script": script, "video_prompt": script})
    for key, value in fields.items():
        setattr(shot, key, value)
    shot.compliance_changes = audit
    await db_session.commit()
    punctuation = next(c for c in audit["narration"]["changes"] if c["old"] == "，")
    title = next(c for c in audit["narration"]["changes"] if c["old"] == "顶级")
    expected = script.replace("顶级", "高端")

    await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": "narration", "change_id": punctuation["id"]}), db=db_session, user=user)
    assert shot.segment_script == shot.video_prompt == expected
    assert shot.narration == narration_from_script(expected)
    for row in change_rows(shot, scope="shot"):
        assert row["matches_current"] is True
        assert next(c for c in row["changes"] if c["old"] == "，")["undone"] is True
    await update_shot(project.id, shot.id, ShotUpdate(compliance_undo={"field": "narration", "change_id": title["id"]}), db=db_session, user=user)
    assert shot.segment_script == shot.video_prompt == expected.replace("高端", "顶级", 1)
    assert shot.narration == narration_from_script(shot.segment_script)
    assert all(row["matches_current"] for row in change_rows(shot, scope="shot"))
