"""Agent Skill：列表、上传、启用、删除。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_agent import (
    AgentSkillListOut,
    AgentSkillOptimizeBody,
    AgentSkillOptimizeOut,
    AgentSkillOut,
    AgentSkillUpdateBody,
    AgentSkillUploadBody,
)
from app.services.agent.compose import skill_to_public_dict
from app.services.agent.optimize import optimize_prompt_with_skills
from app.services.agent.parse import SkillParseError
from app.services.billing import record_llm_chat_line, run_billed_ephemeral
from app.services.billing.http import http_exception_for_value_error
from app.services.agent.store import (
    create_user_skill,
    delete_user_skill,
    get_visible_skill,
    list_visible_skills,
    update_user_skill,
)

router = APIRouter()


def _out(row) -> AgentSkillOut:
    return AgentSkillOut.model_validate(skill_to_public_dict(row))


@router.get(
    "/skills", response_model=AgentSkillListOut,
    summary="查询可用创作技能",
    description="返回系统内置技能及当前用户上传的技能，包含 Markdown 内容、适用任务和启用状态。",
    response_description="当前用户可见的技能列表",
)
async def list_skills(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AgentSkillListOut:
    # 内置 + 当前用户上传的 skill
    rows = await list_visible_skills(db, user.id)
    return AgentSkillListOut(items=[_out(row) for row in rows])


@router.post(
    "/skills/optimize", response_model=AgentSkillOptimizeOut,
    summary="使用创作技能优化提示词",
    description="同步按选定技能和任务类型优化提示词，保留 @asset 引用并记录计费任务。未选择技能时仅去除提示词首尾空白，不调用模型，task_id 返回 null。",
    response_description="优化后的提示词及可选计费任务编号",
)
async def optimize_prompt(
    body: AgentSkillOptimizeBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AgentSkillOptimizeOut:
    # 用勾选 Skill 改写提示词，保留 @asset 引用
    task_name = (body.task or "video_prompt").strip() or "video_prompt"
    skill_ids = list(body.skill_ids or [])
    if not skill_ids:
        prompt = (body.prompt or "").strip()
        return AgentSkillOptimizeOut(prompt=prompt, task_id=None)

    async def _do_optimize() -> str:
        prompt = await optimize_prompt_with_skills(
            db,
            user.id,
            prompt=body.prompt,
            skill_ids=skill_ids,
            task=task_name,
        )
        await record_llm_chat_line(
            db,
            user_id=user.id,
            domain="drama",
            drama_project_id=body.project_id,
        )
        return prompt

    try:
        task, prompt = await run_billed_ephemeral(
            db,
            user,
            domain="drama",
            task_type="skill_optimize",
            executor=_do_optimize,
            payload={"task": task_name, "skill_count": len(skill_ids)},
            drama_project_id=body.project_id,
            commit=True,
        )
    except ValueError as exc:
        raise http_exception_for_value_error(exc) from exc
    return AgentSkillOptimizeOut(prompt=prompt, task_id=task.id)


@router.get(
    "/skills/{skill_id}", response_model=AgentSkillOut,
    summary="获取创作技能详情",
    description="按技能编号 skill_id 获取系统内置或当前用户拥有的技能，不可见或不存在时返回 404。",
    response_description="技能内容、适用任务和启用状态",
)
async def get_skill(
    skill_id: Annotated[int, Path(description="技能编号，可读取内置技能或本人上传的技能")],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AgentSkillOut:
    row = await get_visible_skill(db, user.id, skill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return _out(row)


@router.post(
    "/skills", response_model=AgentSkillOut,
    summary="通过 Markdown 内容创建技能",
    description="从 JSON 请求中的完整 Markdown 解析并创建当前用户的技能。内容不符合技能格式时返回 400，保存后默认启用。",
    response_description="解析并保存后的用户技能",
)
async def upload_skill_markdown(
    body: AgentSkillUploadBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AgentSkillOut:
    # JSON 上传完整 markdown
    try:
        row = await create_user_skill(db, user.id, body.markdown)
    except SkillParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _out(row)


@router.post(
    "/skills/upload", response_model=AgentSkillOut,
    summary="上传 Markdown 技能文件",
    description="通过 multipart/form-data 上传 UTF-8 技能文本，接受 .md、.markdown、.txt 文件，最大 200KB。解析失败或编码、大小不符合要求时返回 400。",
    response_description="从文件解析并保存后的用户技能",
)
async def upload_skill_file(
    file: UploadFile = File(..., description="UTF-8 编码的 .md、.markdown 或 .txt 技能文件，最大 200KB"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AgentSkillOut:
    # 上传 .md 文件
    filename = (file.filename or "").lower()
    if filename and not filename.endswith((".md", ".markdown", ".txt")):
        raise HTTPException(status_code=400, detail="请上传 .md 文件")
    raw = await file.read()
    if len(raw) > 200 * 1024:
        raise HTTPException(status_code=400, detail="文件不能超过 200KB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="文件须为 UTF-8 文本") from exc
    try:
        row = await create_user_skill(db, user.id, text)
    except SkillParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _out(row)


@router.patch(
    "/skills/{skill_id}", response_model=AgentSkillOut,
    summary="修改技能内容或启用状态",
    description="按技能编号 skill_id 更新当前用户的技能正文或启用状态；系统内置技能只允许修改启用开关，不能修改正文。不可见技能返回 404，Markdown 解析失败返回 400。",
    response_description="更新后的技能详情",
)
async def patch_skill(
    skill_id: Annotated[int, Path(description="技能编号，可读取内置技能或本人上传的技能")],
    body: AgentSkillUpdateBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AgentSkillOut:
    row = await get_visible_skill(db, user.id, skill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    if not row.is_builtin and row.user_id != user.id:
        raise HTTPException(status_code=403, detail="不能修改他人 Skill")
    try:
        row = await update_user_skill(db, row, markdown=body.markdown, is_active=body.is_active)
    except SkillParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _out(row)


@router.delete(
    "/skills/{skill_id}",
    summary="删除用户上传的技能",
    description="按技能编号 skill_id 删除当前用户上传的技能。系统内置技能不可删除，只能停用；不可见或不存在时返回 404。",
    response_description="删除成功标记，ok 为 true",
)
async def remove_skill(
    skill_id: Annotated[int, Path(description="技能编号，可读取内置技能或本人上传的技能")],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    row = await get_visible_skill(db, user.id, skill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    if row.is_builtin:
        raise HTTPException(status_code=400, detail="系统内置 Skill 不能删除，可停用")
    if row.user_id != user.id:
        raise HTTPException(status_code=403, detail="不能删除他人 Skill")
    try:
        await delete_user_skill(db, row)
    except SkillParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
