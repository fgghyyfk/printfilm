"""Conversation planning and durable, billed production-stage orchestration.

Every user turn and media stage is a TaskRun. Database row locks serialize
advancement, and the scheduler reconciles committed state after restarts.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Project, ProjectStatus, Template, User
from app.models_creation import CreationMessage, CreationRun, CreationSession
from app.models_tasks import TaskRun
from app.schemas import ProjectOut
from app.schemas_creation import (
    CreationDecision, CreationMessageCreate, CreationMessageOut,
    CreationRunOut, CreationSessionCreate, CreationSessionListItem, CreationSessionListOut, CreationSessionOut,
)
from app.schemas_tasks import TaskCreateRequest, TaskTargetBind
from app.services.creation_templates import CREATION_TEMPLATE_IDS, DEFAULT_CREATION_TEMPLATE_ID
from app.services.tasks.service import ACTIVE_TASK_STATUSES, TERMINAL_TASK_STATUSES, append_task_event, create_task
from app.services.voices import list_voices

logger = logging.getLogger(__name__)
_reconcile_cursor = 0


async def _session(db: AsyncSession, session_id: int, user_id: int, *, lock: bool = False) -> CreationSession:
    stmt = select(CreationSession).where(CreationSession.id == session_id, CreationSession.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="创作会话不存在")
    return row


async def _template(db: AsyncSession, template_id: str | None) -> str:
    tid = template_id or DEFAULT_CREATION_TEMPLATE_ID
    if tid not in CREATION_TEMPLATE_IDS:
        raise HTTPException(status_code=400, detail="请选择可用的动画模板")
    tpl = await db.get(Template, tid)
    if tpl is None or not tpl.is_active:
        raise HTTPException(status_code=400, detail="模板暂不可用")
    return tid


async def session_snapshot(db: AsyncSession, session_id: int, user_id: int) -> CreationSessionOut:
    session = await _session(db, session_id, user_id)
    messages = list((await db.execute(select(CreationMessage).where(
        CreationMessage.session_id == session.id,
    ).order_by(CreationMessage.id))).scalars())
    runs = list((await db.execute(select(CreationRun).where(
        CreationRun.session_id == session.id,
    ).order_by(CreationRun.version))).scalars())
    project_ids = [run.project_id for run in runs if run.project_id is not None]
    projects = {}
    if project_ids:
        rows = (await db.execute(select(Project).where(
            Project.id.in_(project_ids), Project.user_id == user_id,
        ).options(selectinload(Project.shots)).execution_options(populate_existing=True))).scalars()
        for project in rows:
            projects[project.id] = ProjectOut.model_validate(project)
    return CreationSessionOut(
        id=session.id, title=session.title, status=session.status, template_id=session.template_id,
        brief=session.brief or {}, active_run_id=session.active_run_id,
        messages=[CreationMessageOut.model_validate(message) for message in messages],
        runs=[CreationRunOut.model_validate(run).model_copy(update={"project": projects.get(run.project_id)}) for run in runs],
    )


async def list_sessions(db: AsyncSession, user_id: int, limit: int = 5) -> CreationSessionListOut:
    rows = (await db.execute(select(CreationSession).where(CreationSession.user_id == user_id)
                             .order_by(CreationSession.updated_at.desc(), CreationSession.id.desc()).limit(limit))).scalars()
    return CreationSessionListOut(items=[CreationSessionListItem.model_validate(row) for row in rows])


async def create_session(db: AsyncSession, user: User, body: CreationSessionCreate) -> CreationSessionOut:
    # The owner row also serializes duplicate first-message requests before a session exists.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    if body.client_message_id:
        existing = (await db.execute(select(CreationSession).where(
            CreationSession.user_id == user.id, CreationSession.client_request_id == body.client_message_id,
        ))).scalar_one_or_none()
        if existing is not None:
            if body.message and body.message.strip():
                first = (await db.execute(select(CreationMessage).where(
                    CreationMessage.session_id == existing.id,
                    CreationMessage.client_message_id == body.client_message_id,
                ))).scalar_one_or_none()
                if first is not None and first.content != body.message.strip():
                    raise HTTPException(status_code=409, detail="消息编号已使用，请重新发送")
            return await session_snapshot(db, existing.id, user.id)
    session = CreationSession(
        user_id=user.id, client_request_id=body.client_message_id,
        template_id=await _template(db, body.template_id), brief=_brief({}),
    )
    db.add(session)
    await db.flush()
    if body.message and body.message.strip():
        await _submit_message(db, user, session, CreationMessageCreate(
            content=body.message, client_message_id=body.client_message_id or uuid4().hex,
            template_id=body.template_id,
        ))
    else:
        db.add(CreationMessage(session_id=session.id, role="assistant", content="想做什么视频？告诉我主题、受众或故事，我会帮你完成文案并制作成片。"))
    await db.commit()
    await reconcile_creation_sessions(db, session_id=session.id)
    return await session_snapshot(db, session.id, user.id)


async def add_message(db: AsyncSession, user: User, session_id: int, body: CreationMessageCreate) -> CreationSessionOut:
    session = await _session(db, session_id, user.id, lock=True)
    _ensure_conversation_writable(session)
    duplicate = (await db.execute(select(CreationMessage).where(
        CreationMessage.session_id == session.id, CreationMessage.client_message_id == body.client_message_id,
    ))).scalar_one_or_none()
    if duplicate is not None:
        if duplicate.content != body.content:
            raise HTTPException(status_code=409, detail="消息编号已使用，请重新发送")
        return await session_snapshot(db, session.id, user.id)
    await _submit_message(db, user, session, body)
    await db.commit()
    await reconcile_creation_sessions(db, session_id=session.id)
    return await session_snapshot(db, session.id, user.id)


def _session_task_filter(session_id: int):
    return TaskRun.payload["creation_session_id"].as_integer() == session_id


async def _cancel_session_tasks(db: AsyncSession, session: CreationSession) -> None:
    tasks = list((await db.execute(select(TaskRun).where(
        TaskRun.requested_by == session.user_id, _session_task_filter(session.id),
        TaskRun.status.in_(ACTIVE_TASK_STATUSES),
    ).with_for_update())).scalars())
    for task in tasks:
        if task.cancel_requested:
            continue
        task.cancel_requested = True
        task.status = "cancel_requested"
        task.next_action_at = datetime.now(UTC)
        await append_task_event(db, task.id, event_type="task.cancel_requested", status=task.status, message="创作会话取消或已有新需求")
    if session.active_run_id:
        run = await db.get(CreationRun, session.active_run_id)
        if run and run.status == "generating":
            run.status = "cancelled"
            if run.project_id:
                project = await db.get(Project, run.project_id)
                if project and project.status != ProjectStatus.DONE:
                    project.status = ProjectStatus.CANCELLED
                    project.error_msg = "创作已取消"


async def _new_turn_task(db: AsyncSession, user: User, session: CreationSession, message: CreationMessage,
                         *, requested_template_id: str | None = None) -> TaskRun:
    if requested_template_id is None and message.task_id:
        previous_task = await db.get(TaskRun, message.task_id)
        requested_template_id = (previous_task.payload or {}).get("requested_template_id") if previous_task else None
    task = await create_task(db, user, TaskCreateRequest(
        domain="kepu", task_type="creation_turn", defer_activation=True,
        client_request_id=f"creation:{session.id}:message:{message.id}",
        payload={"creation_session_id": session.id, "message_id": message.id,
                 "requested_template_id": requested_template_id},
    ), commit=False)
    message.task_id = task.id
    session.active_turn_task_id = task.id
    return task


async def _submit_message(db: AsyncSession, user: User, session: CreationSession, body: CreationMessageCreate) -> None:
    if body.template_id:
        session.template_id = await _template(db, body.template_id)
    await _cancel_session_tasks(db, session)
    message = CreationMessage(session_id=session.id, role="user", content=body.content,
                              client_message_id=body.client_message_id)
    db.add(message)
    await db.flush()
    session.latest_message_id = message.id
    session.status = "thinking"
    if session.title == "新的创作":
        session.title = body.content[:40]
    await _new_turn_task(db, user, session, message, requested_template_id=body.template_id)


async def cancel_session(db: AsyncSession, user: User, session_id: int) -> CreationSessionOut:
    session = await _session(db, session_id, user.id, lock=True)
    _ensure_conversation_writable(session)
    if session.status != "cancelled":
        await _cancel_session_tasks(db, session)
        session.status = "cancelled"
        db.add(CreationMessage(session_id=session.id, role="system", content="已停止本次创作。已有素材已保存，可重试或继续提出新需求。"))
    await db.commit()
    return await session_snapshot(db, session.id, user.id)


async def retry_session(db: AsyncSession, user: User, session_id: int) -> CreationSessionOut:
    session = await _session(db, session_id, user.id, lock=True)
    _ensure_conversation_writable(session)
    if session.status not in {"failed", "cancelled"}:
        # Idempotent retry clicks cannot enqueue another stage.
        return await session_snapshot(db, session.id, user.id)
    run = await db.get(CreationRun, session.active_run_id) if session.active_run_id else None
    if run and run.source_message_id == session.latest_message_id and run.project_id:
        run.status = "generating"
        run.error_message = None
        run.current_task_id = None
        project = await db.get(Project, run.project_id)
        if project is None:
            raise HTTPException(status_code=409, detail="制作项目已删除，请发送新的需求")
        project.error_msg = None
        session.status = "generating"
    else:
        message = await db.get(CreationMessage, session.latest_message_id) if session.latest_message_id else None
        if message is None:
            session.status = "drafting"
        elif message.response_payload and message.response_payload.get("action") == "ask":
            # A cancelled clarification already has a durable answer; restore it
            # without paying for the same question or leaving a completed turn pending.
            session.status = "drafting"
        else:
            await _new_turn_task(db, user, session, message)
            session.status = "thinking"
    await db.commit()
    await reconcile_creation_sessions(db, session_id=session.id)
    return await session_snapshot(db, session.id, user.id)


def _ensure_conversation_writable(session: CreationSession) -> None:
    if session.status in {"editor_pending", "editor"}:
        raise HTTPException(status_code=409, detail="该作品已转入分步创作，请在作品编辑页继续")


async def open_editor(db: AsyncSession, user: User, session_id: int, *, project_id: int | None = None) -> ProjectOut:
    """Transfer a conversation to the wizard only after all producers settle.

    The session lock serializes this handoff with planning and reconciliation.
    Persisting editor_pending before returning 409 prevents restart/callbacks or
    an old conversation tab from enqueuing another producer during the wait.
    """
    session = await _session(db, session_id, user.id, lock=True)
    editor_project_id = (session.brief or {}).get("editor_project_id")
    project = None
    if project_id is not None:
        # Validate the selected version before cancelling anything. Owner scope
        # alone is insufficient: the project must belong to this conversation.
        project = (await db.execute(select(Project).join(CreationRun, CreationRun.project_id == Project.id).where(
            CreationRun.session_id == session.id, Project.user_id == user.id, Project.id == project_id,
        ).options(selectinload(Project.shots)).with_for_update(of=Project))).scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="该创作版本不存在")
    elif session.status == "editor" and editor_project_id:
        project = (await db.execute(select(Project).where(
            Project.id == editor_project_id, Project.user_id == user.id,
        ).options(selectinload(Project.shots)))).scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="已转入编辑的项目不存在")
    if session.status == "editor" and project is not None and project.creation_session_id is None:
        # Already transferred: a wizard task may now be writing this project.
        # Opening another old link must not reset its status or cancellation.
        return ProjectOut.model_validate(project)

    task_stmt = select(TaskRun).where(
        TaskRun.requested_by == user.id, _session_task_filter(session.id),
    )
    if session.status != "editor":
        await _cancel_session_tasks(db, session)
        session.status = "editor_pending"
    elif project is not None:
        # Other versions have already stopped; inspect only this version's old
        # producers and never cancel new ordinary editor tasks.
        task_stmt = task_stmt.where(TaskRun.project_id == project.id)
    tasks = list((await db.execute(task_stmt.with_for_update().execution_options(populate_existing=True))).scalars())
    if any(task.status not in TERMINAL_TASK_STATUSES or not _settled(task) for task in tasks):
        await db.commit()
        raise HTTPException(status_code=409, detail="正在停止旧创作任务并结算，请稍后重试进入编辑",
                            headers={"Retry-After": "2"})

    # Prefer the newest surviving version; completed frames/audio stay attached
    # to their original project and old completed versions remain accessible.
    if project is None:
        project = (await db.execute(select(Project).join(CreationRun, CreationRun.project_id == Project.id).where(
            CreationRun.session_id == session.id, Project.user_id == user.id,
        ).order_by(CreationRun.version.desc()).limit(1).options(selectinload(Project.shots))
                                  .with_for_update(of=Project))).scalar_one_or_none()
    if project is None:
        message = (await db.execute(select(CreationMessage).where(
            CreationMessage.session_id == session.id, CreationMessage.role == "user",
        ).order_by(CreationMessage.id.desc()).limit(1))).scalar_one_or_none()
        payload = (message.response_payload or {}) if message else {}
        copy_text = str(payload.get("copy_text") or "").strip()
        source_text = copy_text or (message.content.strip() if message else "")
        if len(source_text) < 2:
            await db.rollback()
            raise HTTPException(status_code=400, detail="会话中还没有创作内容，请从新建短片开始")
        brief = _brief(session.brief or {})
        project = Project(
            user_id=user.id, template_id=await _template(db, session.template_id), title=session.title,
            source_type="script" if copy_text else "theme", source_text=source_text, pipeline_mode="image_text",
            resolution_mode="hd", output_ratio=brief["output_ratio"], voice_id=brief["voice_id"],
            target_duration_sec=brief["target_duration_sec"], status=ProjectStatus.DRAFT,
        )
        db.add(project)
        await db.flush()
    else:
        if project.status != ProjectStatus.DONE or not project.final_video_url:
            from app.services.kepu_stages import resolve_kepu_billing_phase
            phase = resolve_kepu_billing_phase(project)
            project.status = {"script": ProjectStatus.DRAFT, "assets": ProjectStatus.SCRIPT_READY,
                              "videos": ProjectStatus.IMAGE_READY, "compose": ProjectStatus.IMAGE_READY}[phase]
        project.error_msg = None
    project.creation_session_id = None
    from app.services import pipeline
    pipeline.clear_pipeline_cancellation(project.id)
    session.status = "editor"
    session.brief = {**(session.brief or {}), "editor_project_id": editor_project_id or project.id}
    await db.commit()
    project = (await db.execute(select(Project).where(Project.id == project.id)
                               .options(selectinload(Project.shots)))).scalar_one()
    return ProjectOut.model_validate(project)


def _brief(raw: dict) -> dict:
    try:
        duration = int(raw.get("target_duration_sec", 60))
    except (TypeError, ValueError):
        duration = 60
    ratio = str(raw.get("output_ratio") or "9:16")
    voice = str(raw.get("voice_id") or "")
    valid_voices = {preset["id"] for preset in list_voices()}
    return {
        "target_duration_sec": max(15, min(duration, 180)),
        "output_ratio": ratio if ratio in {"9:16", "16:9", "1:1"} else "9:16",
        "voice_id": voice if voice in valid_voices else "",
        **{key: str(raw.get(key) or "")[:400] for key in ("topic", "audience", "tone")},
    }


_PLANNER_SYSTEM = """你是动画短视频创作 Agent。根据完整对话理解用户需求，产出可直接朗读的中文文案。
只输出 JSON：action 为 ask 或 produce，reply 为给用户的简短说明，title 为作品标题，copy_text 为最终旁白文案，
template_id 为动画模板ID，brief 为 {topic,audience,tone,target_duration_sec,output_ratio,voice_id}。
用户主题不明确、连要讲什么都不知道时，action=ask，一次只问一个关键问题。只要主题/故事明确，
即自动补全合理的受众、语气和结构，action=produce，不问是否开始、不要求确认文案/分镜/素材。
默认普通话、约60秒、9:16，按每秒约4个汉字控制文案。用户给出时长时遵循15~180秒。
用户修改先前作品时必须结合历史文案重新产出完整文案。风格只允许3D动画、二维动漫、绘本、水墨，禁止真人写实。
template_id 只能取 available_templates 中的ID；用户提到国风/水墨、二维动漫、绘本、3D时正确切换。
voice_id 只能取 available_voices 中的ID，未指定时沿用 brief.voice_id 或空字符串；用户换男声/女声或指定音色时选择对应预设。
不编造商店地址、功效、价格等事实，未提供的具体信息不要虚构；无需这些信息也能成片时采用通用表达。
produce 时 copy_text 仅包含旁白正文（不得放运镜提示、markdown或分镜编号），至少20字。
ask 时 copy_text 为空。不要宣称已经生成了图片或视频，媒体会在文案产出后由系统自动制作。"""


def _requested_preferences(text: str) -> tuple[dict, str | None]:
    """Honor explicit controls even when a text model omits them in its JSON."""
    brief: dict = {}
    seconds = re.search(r"(\d{1,3})\s*(?:秒|seconds?\b)", text, re.I)
    minutes = re.search(r"(\d(?:\.\d)?)\s*分钟", text)
    if seconds:
        brief["target_duration_sec"] = int(seconds.group(1))
    elif minutes:
        brief["target_duration_sec"] = round(float(minutes.group(1)) * 60)
    else:
        for phrase, duration in (("半分钟", 30), ("一分钟", 60), ("一分半", 90), ("两分钟", 120), ("二分钟", 120), ("三分钟", 180)):
            if phrase in text:
                brief["target_duration_sec"] = duration
                break
    if "横屏" in text or "16:9" in text:
        brief["output_ratio"] = "16:9"
    elif "竖屏" in text or "9:16" in text:
        brief["output_ratio"] = "9:16"
    voices = list_voices()
    genders = list(re.finditer(r"(?:不要|不用)?[男女]声", text))
    if genders:
        mention = genders[-1].group()
        male = "男声" in mention
        if mention.startswith(("不要", "不用")):
            male = not male
        brief["voice_id"] = next(preset["id"] for preset in voices if preset["gender"] == ("male" if male else "female"))
    for preset in voices:
        if preset["label"].split(" · ")[0] in text:
            brief["voice_id"] = preset["id"]
    styles = [(match.start(), tid) for pattern, tid in (
        (r"3d|三维", "anim_3d"), (r"二维|日漫|动漫", "soft_anime"),
        (r"绘本|插画", "kids_flat"), (r"国风|水墨|古风", "ink_guofeng"),
    ) for match in re.finditer(pattern, text, re.I)]
    return brief, max(styles)[1] if styles else None


def _mock_decision(history: list[dict], brief: dict) -> CreationDecision:
    latest = next((item["content"] for item in reversed(history) if item["role"] == "user"), "")
    if (len(latest.strip()) < 5 and not brief.get("topic")) or latest.strip() in {"做个视频", "帮我做视频", "我想做一个视频", "帮我做一个视频"}:
        return CreationDecision(action="ask", reply="你想讲哪个主题或故事？例如：给小学生讲为什么天空是蓝色的。", brief=brief)
    revision = brief.get("topic") and any(word in latest for word in ("改成", "改为", "换成", "换男声", "换女声", "调整", "保持"))
    topic = (brief["topic"] if revision else latest.strip())[:60]
    preferences, template = _requested_preferences(latest)
    merged_brief = _brief({**brief, **preferences, "topic": topic})
    copy_text = (
        f"{topic}。一个小小的问题，也能打开认识世界的新视角。"
        "先从身边最熟悉的场景开始，留意那些平时没有注意到的细节。"
        "把复杂的过程拆成简单的几步，再通过一个直观的例子理解它。"
        "当你知道其中的联系，就会发现，好奇心正是探索的第一步。"
        "下次遇到同样的情景，不妨换一个角度观察，把你的发现分享给身边的人。"
        "观察并不需要复杂的工具，从一个提问开始，就能找到新的线索。"
        "试着把看到的现象记录下来，将前后的变化放在一起比较。"
        "遇到暂时不理解的地方，可以重新看看每个步骤，也可以和朋友一起讨论。"
        "每一次认真观察，都让我们离答案更近一点。现在，就带着这个问题，开始你自己的探索吧。"
    )
    target_chars = merged_brief["target_duration_sec"] * 4
    while len(copy_text) < target_chars:
        copy_text += "把新的发现与原来的想法比较，寻找它们之间的联系，再用自己的话讲给朋友听。"
    if len(copy_text) > target_chars + 35:
        end = copy_text.rfind("。", 20, target_chars + 15)
        copy_text = copy_text[:end + 1] if end > 20 else copy_text[:target_chars] + "。"
    return CreationDecision(action="produce", title=topic[:40], template_id=template,
                            reply="文案已经整理好，我会继续生成分镜、画面和配音，为你制作成片。",
                            copy_text=copy_text, brief=merged_brief)


async def execute_creation_turn(task: TaskRun) -> dict:
    payload = task.payload or {}
    session_id, message_id = int(payload["creation_session_id"]), int(payload["message_id"])
    async with AsyncSessionLocal() as db:
        session = await _session(db, session_id, task.requested_by)
        message = await db.get(CreationMessage, message_id)
        if message is None or session.latest_message_id != message_id or session.status in {"cancelled", "editor_pending", "editor"}:
            return {"cancelled": True}
        if message.response_payload is not None:
            return {"ok": True, "restored": True}
        rows = list((await db.execute(select(CreationMessage).where(
            CreationMessage.session_id == session_id, CreationMessage.id <= message_id,
        ).order_by(CreationMessage.id.desc()).limit(40))).scalars())
        recent = []
        remaining = 30000
        for row in rows:
            if remaining <= 0:
                break
            content = row.content[:min(12000, remaining)]
            recent.append({"role": row.role, "content": content})
            remaining -= len(content)
        history = list(reversed(recent))
        context = {
            "history": history, "brief": session.brief or {}, "template_id": session.template_id,
            "available_templates": {"anim_3d": "3D动画", "soft_anime": "二维动漫", "kids_flat": "绘本插画", "ink_guofeng": "国风水墨"},
            "available_voices": [{key: preset[key] for key in ("id", "label", "gender")} for preset in list_voices()],
        }
    if get_settings().ark_mock:
        decision = _mock_decision(history, context["brief"])
    else:
        from app.services.drama.llm import drama_chat_json
        decision = CreationDecision.model_validate(await drama_chat_json(
            _PLANNER_SYSTEM, json.dumps(context, ensure_ascii=False), temperature=0.6, max_tokens=5000,
        ))
    if decision.action == "produce" and len(decision.copy_text) < 20:
        raise ValueError("文案生成不完整，请重试")
    if decision.action == "ask" and not decision.reply.strip():
        raise ValueError("创作助手未返回有效问题，请重试")
    async with AsyncSessionLocal() as db:
        session = await _session(db, session_id, task.requested_by, lock=True)
        message = await db.get(CreationMessage, message_id)
        if message.response_payload is not None:
            return {"ok": True, "restored": True}
        if not get_settings().ark_mock:
            from app.services.billing import record_line
            await record_line(db, user_id=task.requested_by, billing_key="llm_chat", model=get_settings().model_llm,
                              estimated=True, task_run_id=task.id, domain="kepu")
        live_task = await db.get(TaskRun, task.id)
        if session.latest_message_id != message_id or session.status in {"cancelled", "editor_pending", "editor"} or live_task.cancel_requested:
            await db.commit()
            return {"cancelled": True}
        preferences, requested_style = _requested_preferences(message.content)
        brief = _brief({**(session.brief or {}), **decision.brief, **preferences})
        template_id = payload.get("requested_template_id") or requested_style or decision.template_id or session.template_id
        session.template_id = await _template(db, template_id)
        session.brief = brief
        message.response_payload = decision.model_dump()
        reply = decision.reply.strip()
        if decision.action == "ask":
            session.status = "drafting"
        else:
            session.title = decision.title.strip() or session.title
            project = Project(
                user_id=session.user_id, template_id=session.template_id, title=session.title,
                source_type="script", source_text=decision.copy_text, pipeline_mode="image_text",
                output_ratio=brief["output_ratio"], resolution_mode="hd", status=ProjectStatus.DRAFT,
                creation_session_id=session.id, target_duration_sec=brief["target_duration_sec"],
                voice_id=brief["voice_id"],
                extra_prompt=f"动画短片，禁止真人摄影。目标时长约{brief['target_duration_sec']}秒。",
            )
            db.add(project)
            await db.flush()
            version = int((await db.scalar(select(func.max(CreationRun.version)).where(CreationRun.session_id == session.id))) or 0) + 1
            run = CreationRun(session_id=session.id, source_message_id=message.id, version=version,
                              copy_text=decision.copy_text, project_id=project.id, current_task_id=task.id)
            db.add(run)
            await db.flush()
            session.active_run_id = run.id
            session.status = "generating"
            reply = (reply or "文案已准备好，正在为你制作视频。") + "\n\n" + decision.copy_text
        db.add(CreationMessage(session_id=session.id, role="assistant", content=reply,
                               reply_to_message_id=message.id))
        await db.commit()
    return {"ok": True, "action": decision.action, "creation_session_id": session_id,
            "kind": "text", "text_content": reply}


def _settled(task: TaskRun) -> bool:
    return task.billing_status not in {"frozen", "reserved"}


def _fail_session(db: AsyncSession, session: CreationSession, run: CreationRun | None, message: str) -> None:
    if session.status != "failed":
        db.add(CreationMessage(session_id=session.id, role="system", content=f"制作暂时停下了：{message}。已有素材已保留，重试会继续完成。"))
    session.status = "failed"
    if run:
        run.status = "failed"
        run.error_message = message[:2000]


async def _advance_session(db: AsyncSession, session: CreationSession) -> bool:
    if session.status not in {"thinking", "generating"}:
        return False
    tasks = list((await db.execute(select(TaskRun).where(
        TaskRun.requested_by == session.user_id, _session_task_filter(session.id),
    ))).scalars())
    by_id = {task.id: task for task in tasks}
    if session.status == "thinking":
        turn = by_id.get(session.active_turn_task_id)
        if turn is None:
            _fail_session(db, session, None, "未找到创作任务")
            return True
        if turn.status in {"failed", "cancelled"}:
            _fail_session(db, session, None, turn.error_message or "创作任务已停止")
            return True
        # New requirements wait until every previous producer has stopped writing.
        blockers = [task for task in tasks if task.id != turn.id and (
            task.status not in TERMINAL_TASK_STATUSES or not _settled(task)
        )]
        if turn.status == "pending" and turn.next_action_at is None and not blockers:
            turn.next_action_at = datetime.now(UTC)
            return True
        return False
    run = await db.get(CreationRun, session.active_run_id) if session.active_run_id else None
    if run is None or run.status != "generating":
        return False
    task = by_id.get(run.current_task_id)
    if task:
        if task.status not in TERMINAL_TASK_STATUSES or not _settled(task):
            return False
        if task.status != "succeeded":
            _fail_session(db, session, run, task.error_message or "生成任务已停止")
            return True
    elif any(task.status not in TERMINAL_TASK_STATUSES or not _settled(task) for task in tasks):
        return False
    project = (await db.execute(select(Project).where(Project.id == run.project_id).options(
        selectinload(Project.shots), selectinload(Project.template),
    ).execution_options(populate_existing=True))).scalar_one_or_none()
    if project is None:
        _fail_session(db, session, run, "制作项目已删除")
        return True
    if project.status == ProjectStatus.DONE and project.final_video_url:
        run.phase, run.status = "done", "completed"
        session.status = "completed"
        db.add(CreationMessage(session_id=session.id, role="assistant", content="视频制作完成，可以直接播放或下载。还想调整内容的话，继续告诉我。"))
        return True
    from app.services.kepu_stages import resolve_kepu_billing_phase
    phase = resolve_kepu_billing_phase(project)
    if phase == "videos":
        _fail_session(db, session, run, "当前制作模式不正确")
        return True
    user = await db.get(User, session.user_id)
    # Explicit retry reaches here only after old producers have become terminal.
    # Restore the stage status now, before the newly queued handler reads it.
    from app.services import pipeline
    pipeline.clear_pipeline_cancellation(project.id)
    try:
        async with db.begin_nested():
            next_task = await create_task(db, user, TaskCreateRequest(
                domain="kepu", task_type="project_compose_only" if phase == "compose" else "project_pipeline",
                project_id=project.id,
                payload={"project_id": project.id, "phase": phase, "pipeline_mode": "image_text",
                         "creation_session_id": session.id, "creation_run_id": run.id},
                dedupe_key=f"creation:{session.id}:run:{run.id}:{phase}",
                targets=[TaskTargetBind(target_type="project", target_id=project.id)],
            ), commit=False)
    except (ValueError, HTTPException) as exc:
        _fail_session(db, session, run, str(exc.detail if isinstance(exc, HTTPException) else exc))
        return True
    run.phase, run.current_task_id = phase, next_task.id
    project.status = {"script": ProjectStatus.SCRIPTING, "assets": ProjectStatus.IMAGING, "compose": ProjectStatus.COMPOSING}[phase]
    project.error_msg = None
    return True


async def reconcile_creation_sessions(db: AsyncSession, *, session_id: int | None = None, limit: int = 32) -> int:
    global _reconcile_cursor
    stmt = select(CreationSession).where(CreationSession.status.in_(("thinking", "generating")))
    if session_id is not None:
        stmt = stmt.where(CreationSession.id == session_id)
    scan = stmt if session_id is not None else stmt.where(CreationSession.id > _reconcile_cursor)
    rows = list((await db.execute(scan.order_by(CreationSession.id).limit(limit).with_for_update(skip_locked=True)
                                  .execution_options(populate_existing=True))).scalars())
    if session_id is None and not rows and _reconcile_cursor:
        _reconcile_cursor = 0
        rows = list((await db.execute(stmt.order_by(CreationSession.id).limit(limit).with_for_update(skip_locked=True)
                                      .execution_options(populate_existing=True))).scalars())
    if session_id is None and rows:
        _reconcile_cursor = rows[-1].id
    changed = 0
    for session in rows:
        changed += int(await _advance_session(db, session))
    await db.commit()
    return changed


async def reconcile_after_task(task: TaskRun) -> None:
    session_id = (task.payload or {}).get("creation_session_id")
    if not session_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            await reconcile_creation_sessions(db, session_id=int(session_id))
    except Exception:
        # The periodic reconciler is the durable fallback if this notification is lost.
        logger.exception("creation completion reconcile failed session=%s", session_id)
