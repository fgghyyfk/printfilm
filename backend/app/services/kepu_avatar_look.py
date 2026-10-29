"""数字人「只取人脸」的形象与场景：定妆照、口播分段（含场景 + 重点文字）、每段场景图。

流程：上传人脸照 → generate_look() 按风格生成定妆照（新衣服、新姿势、纯色背景）→
write_script() 让文本模型写分段口播，每段带场景描述与 1–3 个重点词 →
ensure_scene_images() 以定妆照为参考，每段重画一张「同一个人 + 该段场景」的图，之后再逐段对口型。

改图 / 文本都走 avatar_ai（OpenAI 兼容，默认 promptoll）；未配置时由调用方回退到原有逻辑。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import Project, ProjectStatus, Shot, ShotStatus
from app.services import avatar_ai, storage

logger = logging.getLogger(__name__)

# 定妆照风格：id → (中文名, 服装/造型描述)。只换脸以外的部分，脸和年龄保持原样
LOOK_STYLES: dict[str, tuple[str, str]] = {
    "casual": ("清爽休闲", "a clean, bright casual outfit (light knit top or simple T-shirt), neat hair"),
    "school": ("校服学生", "a tidy school uniform with a collared shirt, neat hair, student look"),
    "lab": ("科学小研究员", "a white lab coat over a light shirt, a small name badge, curious scientist look"),
    "host": ("知识主播", "smart business-casual clothing like a TV science show host, polished look"),
    "outdoor": ("户外探索", "an outdoor explorer outfit: light jacket, small backpack straps, adventurous look"),
}
DEFAULT_LOOK_STYLE = "casual"

# 对口型对画面的要求：正脸、闭嘴、上半身、脸部清晰，手不要挡脸
_TALKING_FRAME = (
    "Upper body from the waist up, centered, facing the camera and looking at the viewer, "
    "mouth gently closed with a warm natural expression, hands relaxed below the chest, "
    "face clearly visible and sharp, the face takes about one quarter of the frame width, "
    "vertical 9:16 portrait, photorealistic, natural skin texture. No text, no watermark, no logo."
)
SCENE_CONCURRENCY = 2
_PAST_ASSETS = {ProjectStatus.VIDEO_READY, ProjectStatus.AUDITING, ProjectStatus.DONE, ProjectStatus.REJECTED,
                ProjectStatus.FAILED}


def look_prompt(style: str) -> str:
    """定妆照提示词：只保留参考图里的脸（五官、脸型、肤色、年龄），衣服姿势背景全部重新生成。"""
    _, outfit = LOOK_STYLES.get(style, LOOK_STYLES[DEFAULT_LOOK_STYLE])
    return (
        "Create a new photo of the SAME person as in the reference image. Keep only their identity: exact facial "
        "features, face shape, eye shape, skin tone, hairstyle color and apparent age must stay identical. "
        f"Replace everything else: dress them in {outfit}. Plain soft light-gray studio background, "
        f"even soft front lighting. {_TALKING_FRAME}"
    )


def scene_prompt(scene: str) -> str:
    """场景图提示词：人物（脸、发型、服装）完全不变，只把背景与光线换成该段场景。"""
    scene = (scene or "").strip() or "a bright, clean educational studio"
    return (
        "Keep the person from the reference image exactly the same: same face, hairstyle, outfit and body "
        f"proportions. Change only the background and lighting to this scene: {scene}. The background should "
        "clearly illustrate the scene and stay slightly soft so the person stands out, lighting on the person "
        f"matches the scene. {_TALKING_FRAME}"
    )


def split_highlights(raw: str | list | None) -> list[str]:
    """把重点文字统一成列表：去空、去重、每个最多 8 字、最多 3 个。"""
    items = raw if isinstance(raw, list) else re.split(r"[|｜、,，]", str(raw or ""))
    out: list[str] = []
    for item in items:
        text = re.sub(r"\s+", "", str(item or ""))[:8]
        if text and text not in out:
            out.append(text)
    return out[:3]


_SCRIPT_SYSTEM = """你是儿童/大众科普短视频编剧，为「真人数字人出镜口播」写分段脚本。
要求：
- 口语化、有画面感，像在跟观众聊天；第一段开头要有吸引人的提问或反常识点
- 每段讲一个小点，口播 35~75 字，段与段之间自然衔接
- 每段给一个与内容强相关、能直观帮助理解的出镜场景（人物站在这个场景前讲解），用英文描述背景环境，不要描述人物
- 每段给 1~3 个重点词（每个不超过 6 个字），是这一段最该被记住的概念或数字，必须原样出现在该段口播里
只输出 JSON：{"title": "视频标题(≤12字)", "segments": [{"title": "小标题(≤10字)", "narration": "口播",
"scene": "English background description", "keywords": ["重点词"]}]}"""


def script_user_prompt(source_text: str, source_type: str, seg_range: tuple[int, int]) -> str:
    """拼文案请求：主题模式自由创作；口播稿模式只切分、不改写原文。"""
    lo, hi = seg_range
    if source_type == "script":
        return (f"下面是已经写好的口播稿，请按意思切成 {lo}~{hi} 段，口播必须一字不改地使用原文（可去掉段落符号），"
                f"再为每段补小标题、场景和重点词。\n\n口播稿：\n{source_text}")
    return f"主题：{source_text}\n请写 {lo}~{hi} 段。"


def normalize_segments(data: dict) -> list[dict]:
    """校验并清洗模型返回的分段，丢弃没有口播的段。"""
    segs: list[dict] = []
    for seg in data.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        narration = str(seg.get("narration") or "").strip()
        if not narration:
            continue
        keywords = [k for k in split_highlights(seg.get("keywords")) if k in narration] \
            or split_highlights(seg.get("keywords"))[:1]
        segs.append({
            "title": str(seg.get("title") or "").strip()[:20],
            "narration": narration,
            "scene": str(seg.get("scene") or "").strip()[:600],
            "keywords": keywords,
        })
    if not segs:
        raise RuntimeError("文案生成失败：模型没有返回有效的分段")
    return segs


async def write_script(project_id: int) -> None:
    """数字人模式的写稿阶段：一次请求得到分段口播 + 场景 + 重点词，替换项目全部分镜。"""
    from app.config import get_settings
    from app.services.seedance_segments import estimate_narration_duration, suggested_avatar_shot_range

    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        source_text, source_type = project.source_text, project.source_type
    max_sec = max(6, int(get_settings().avatar_segment_max_sec))
    seg_range = (4, 6) if source_type == "theme" else suggested_avatar_shot_range(source_text, max_sec=max_sec)
    data = await avatar_ai.chat_json(_SCRIPT_SYSTEM, script_user_prompt(source_text, source_type, seg_range))
    segs = normalize_segments(data)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Project).where(Project.id == project_id)
                                  .options(selectinload(Project.shots)))
        project = result.scalar_one()
        for shot in list(project.shots):
            await db.delete(shot)
        await db.flush()
        if source_type == "theme" and data.get("title") and project.title in ("", "未命名作品"):
            project.title = str(data["title"]).strip()[:60]
        for i, seg in enumerate(segs, start=1):
            db.add(Shot(
                project_id=project.id, shot_no=i,
                duration=float(min(max_sec, max(3, estimate_narration_duration(seg["narration"])))),
                narration=seg["narration"], overlay_title=seg["title"], img_prompt=seg["scene"],
                video_prompt="", segment_script="", camera="static", highlights="|".join(seg["keywords"]),
                image_url=project.avatar_image_url, status=ShotStatus.PENDING,
            ))
        project.status = ProjectStatus.SCRIPT_READY
        project.progress = 15
        await db.commit()
    logger.info("avatar script project=%s segments=%d", project_id, len(segs))


async def generate_look(project_id: int, style: str, model: str | None = None) -> str:
    """由人脸照生成定妆照，设为项目形象；旧的场景图与对口型视频全部作废。返回新定妆照 URL。"""
    from app.services.kepu_avatar import portrait_path

    style = style if style in LOOK_STYLES else DEFAULT_LOOK_STYLE
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        face_url = project.avatar_face_url or project.avatar_image_url
        if not face_url:
            raise RuntimeError("请先上传人脸照片")
        face = await portrait_path(project, url=face_url)
        model = model or project.avatar_image_model
    dest = storage.project_dir(project_id) / f"avatar_look_{int(time.time())}.png"
    await avatar_ai.edit_image(face, look_prompt(style), dest, model=model)
    url = storage.publish_local(dest)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Project).where(Project.id == project_id)
                                  .options(selectinload(Project.shots)))
        project = result.scalar_one()
        project.avatar_image_url = url
        project.avatar_look_style = style
        project.cover_url = url
        project.final_video_url = None
        for shot in project.shots or []:
            shot.image_url = url
            shot.video_url = None
            shot.avatar_job_id = None
        # 已经出过视频/成片的项目退回「素材就绪」，继续时会重画场景图再对口型
        if project.shots and project.status in _PAST_ASSETS:
            project.status = ProjectStatus.IMAGE_READY
        await db.commit()
    return url


def needs_scene(shot: Shot, look_url: str | None) -> bool:
    """该段是否还要画场景图：有场景描述，且当前画面仍是定妆照占位或文件已丢。"""
    if not (shot.img_prompt or "").strip():
        return False
    url = (shot.image_url or "").strip()
    if not url or url == (look_url or ""):
        return True
    local = storage.local_path_from_url(url)
    return bool(local) and not local.exists()


async def ensure_scene_images(project_id: int, look: Path, on_progress=None,
                              only_shot_ids: set[int] | None = None) -> None:
    """为缺场景图的段重画「同一个人 + 该段场景」，最多并发 SCENE_CONCURRENCY 张；新图会作废该段旧视频。

    only_shot_ids 给定时只处理这些段（单段重画用）。
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Project).where(Project.id == project_id)
                                  .options(selectinload(Project.shots)))
        project = result.scalar_one()
        model = project.avatar_image_model
        todo = [(s.id, s.shot_no, s.img_prompt) for s in sorted(project.shots, key=lambda s: s.shot_no)
                if needs_scene(s, project.avatar_image_url)
                and (only_shot_ids is None or s.id in only_shot_ids)]
    if not todo:
        return
    sem = asyncio.Semaphore(SCENE_CONCURRENCY)
    done = 0

    async def one(shot_id: int, shot_no: int, scene: str) -> None:
        nonlocal done
        async with sem:
            dest = storage.project_dir(project_id) / f"scene_{shot_no:03d}_{int(time.time() * 1000)}.png"
            try:
                await avatar_ai.edit_image(look, scene_prompt(scene), dest, model=model)
            except Exception as exc:
                raise RuntimeError(f"第 {shot_no} 段场景图生成失败：{str(exc)[:200]}") from exc
            url = storage.publish_local(dest)
            async with AsyncSessionLocal() as db:
                row = await db.get(Shot, shot_id)
                if row:
                    row.image_url = url
                    row.video_url = None
                    row.avatar_job_id = None
                    await db.commit()
            done += 1
            if on_progress:
                await on_progress(done, len(todo), shot_no)

    results = await asyncio.gather(*(one(*t) for t in todo), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    if errors:
        raise errors[0]
