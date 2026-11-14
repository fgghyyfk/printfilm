"""商品带货动态展示：视频形式、动态镜头选择、商品动作白名单、满幅首帧与视频请求参数。

静图口播仍走 image_text；混合展示/全动态走 full 流水线，静图镜头用
video_skip_reason=static 标记（不提交视频、不预扣、合成时推拉）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from app.config import get_settings
from app.services import storage
from app.services.daihuo.frames import frame_size

logger = logging.getLogger(__name__)

VIDEO_FORMS = ("static", "mixed", "motion")

# 商品动作白名单：key → (短名, 视频描述)。只允许小幅、可控的运动，降低商品走样
MOTION_ACTIONS: dict[str, tuple[str, str]] = {
    "push_in": ("缓慢推近", "镜头从中景缓慢推近商品，聚焦主体细节"),
    "orbit": ("环绕展示", "镜头围绕商品缓慢水平环绕约 20 度，展示侧面轮廓"),
    "macro": ("微距扫过", "微距镜头沿商品表面缓慢平移，展示材质与做工细节"),
    "light_sweep": ("光影扫过", "一道柔和的光线从一侧缓慢扫过商品表面，商品与镜头保持稳定"),
    "pull_out": ("拉远全景", "镜头从商品近景缓慢拉远，交代商品所在的使用场景"),
    "demo": ("使用演示", "演示商品本身支持的使用动作，动作缓慢、幅度小"),
}
DEFAULT_FIRST_ACTION = "push_in"
DEFAULT_ACTION = "light_sweep"

# 首帧裁切保留比例下限：低于此值说明裁切会切掉商品，改走扩图
CROP_MIN_KEEP = 0.75

MOTION_FRAME_PREFIX = "motion_"

PROMPT_GUARD = (
    "商品的外形、颜色、比例、材质与 logo 必须与首帧完全一致，不变形、不融化、不新增部件；"
    "画面中不新增任何文字、字幕、水印和其他品牌标识；背景、光线与色调保持首帧风格；"
    "镜头平稳，无人物出镜，无快速运动。"
)

OUTPAINT_PROMPT = (
    "把这张图扩展为完整的满幅画面：中间清晰区域里的商品与场景完全保持原样（外形、颜色、logo、文字都不改），"
    "把四周模糊、压暗的填充区域替换为与中间场景自然衔接的真实背景，光线和透视一致；"
    "不新增文字、水印、其他商品或品牌标识，不出现边框和拼接痕迹。"
)
OUTPAINT_NEGATIVE = "文字，水印，logo 变化，商品变形，边框，拼接痕迹，模糊边缘"


def normalize_form(value: str | None) -> str:
    """视频形式归一：未知值回落静图口播。"""
    v = (value or "").strip().lower()
    return v if v in VIDEO_FORMS else "static"


def pipeline_mode_for(form: str) -> str:
    """静图口播走 image_text，其余走 full（逐镜图生视频）。"""
    return "image_text" if normalize_form(form) == "static" else "full"


def form_of_project(project: Any) -> str:
    """由项目推回视频形式：image_text=静图；full 下全部动态=全动态，否则混合。"""
    if (getattr(project, "pipeline_mode", "") or "") != "full":
        return "static"
    shots = list(getattr(project, "shots", None) or [])
    if shots and all(getattr(s, "video_skip_reason", None) != "static" for s in shots):
        return "motion"
    return "mixed"


def is_daihuo(project: Any) -> bool:
    """是否商品带货项目（套图或批量图片来源都用带货模板）。"""
    from app.services.templates_seed_daihuo import DAIHUO_TEMPLATE_ID

    return bool(getattr(project, "ecom_set_id", None)) or getattr(project, "template_id", None) == DAIHUO_TEMPLATE_ID


def motion_unavailable_reason() -> str | None:
    """动态展示的前置条件：视频模型需拿到首帧图（公网 OSS 或允许内嵌）。返回 None 表示可用。"""
    s = get_settings()
    if s.ark_mock or s.oss_enabled or s.daihuo_video_inline_refs:
        return None
    return "动态展示需要启用 OSS 存储，请联系管理员"


def action_key(raw: Any, *, first: bool) -> str:
    """模型给的动作 key 不在白名单时回落默认动作。"""
    key = str(raw or "").strip()
    if key in MOTION_ACTIONS:
        return key
    return DEFAULT_FIRST_ACTION if first else DEFAULT_ACTION


def pick_motion_positions(flags: list[bool], form: str, *, max_shots: int | None = None) -> set[int]:
    """按视频形式选动态镜头（位置下标）。

    全动态：全部；混合：首镜必选，再按模型建议顺序补足，不超过 max_shots；
    模型一个都没建议时补第二镜，保证混合片至少有两处动态（镜头数够时）。
    """
    form = normalize_form(form)
    n = len(flags)
    if form == "static" or n == 0:
        return set()
    if form == "motion":
        return set(range(n))
    cap = max(1, int(max_shots or get_settings().daihuo_motion_max_shots))
    picked = [0]
    for pos in range(1, n):
        if len(picked) >= cap:
            break
        if flags[pos]:
            picked.append(pos)
    if len(picked) == 1 and n > 2 and cap > 1:
        picked.append(1)
    return set(picked)


def build_video_prompt(action: str, detail: str = "") -> str:
    """组装带货动态镜头的视频提示词：动作 + 具体描述 + 保真约束（纯文本，不走科普时间轴）。"""
    _, desc = MOTION_ACTIONS.get(action, MOTION_ACTIONS[DEFAULT_ACTION])
    parts = [f"商品展示镜头：{desc}。"]
    extra = (detail or "").strip().rstrip("。")
    if extra:
        parts.append(f"{extra}。")
    parts.append(PROMPT_GUARD)
    return "".join(parts)


PADDED_FRAME_MARK = "/daihuo/frame_"


def is_motion_frame(url: str | None) -> bool:
    """分镜图能否直接当动态首帧：只有带货补边图（daihuo/frame_*，四周模糊）需要先扩图；
    裁切/扩图得到的 motion_*、用户重绘出的整幅新图都可以直接用。"""
    text = str(url or "")
    return bool(text) and PADDED_FRAME_MARK not in text


def crop_keep_ratio(size: tuple[int, int], ratio: str) -> float:
    """原图中心裁成目标画幅时，被裁那条边能保留的比例（1 表示画幅一致）。"""
    w, h = size
    tw, th = frame_size(ratio)
    target = tw / th
    current = w / h
    return min(current, target) / max(current, target)


def crop_full_bleed(src: Path, dest: Path, *, ratio: str) -> bool:
    """画幅接近时中心裁成满幅首帧并缩放到视频尺寸；裁切会切掉太多时返回 False（改走扩图）。"""
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGB")
    if crop_keep_ratio(img.size, ratio) < CROP_MIN_KEEP:
        return False
    out = ImageOps.fit(img, frame_size(ratio), method=Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, format="JPEG", quality=92)
    return True


def motion_frame_path(project_id: int, shot_no: int) -> Path:
    """满幅首帧的落盘路径（文件名前缀用于识别）。"""
    return storage.project_dir(project_id) / "daihuo" / f"{MOTION_FRAME_PREFIX}{shot_no:02d}_{uuid.uuid4().hex[:6]}.jpg"


async def outpaint_frame(project_id: int, shot: Any, *, ratio: str) -> str:
    """把补边图扩成满幅首帧并记账，返回本站地址。mock 下直接复制补边图。"""
    src_url = str(shot.image_url or "")
    dest = motion_frame_path(project_id, int(shot.shot_no))
    s = get_settings()
    if s.ark_mock:
        local = storage.local_path_from_url(src_url)
        if not local or not local.is_file():
            raise ValueError("分镜图不存在，无法生成动态首帧")
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, local, dest)
        return storage.rel_static_url(dest)
    from app.services.ark import get_ark
    from app.services.ecom_jobs.kinds import image_geometry
    from app.services.pipeline import _record_seedream_usage

    aspect, size = image_geometry(ratio, "2k", (0, 0))
    result = await get_ark().gen_image(
        OUTPAINT_PROMPT, OUTPAINT_NEGATIVE, [storage.to_public_url(src_url)],
        project_id=project_id, shot_no=int(shot.shot_no), size=size, aspect_ratio=aspect, structured_refs=True,
    )
    await _record_seedream_usage(project_id, image_result=result, model=s.model_image, shot_id=int(shot.id))
    url = result.local_url or result.remote_url
    if not url:
        raise RuntimeError("扩图未返回图片")
    saved = await storage.ensure_local_media(url, dest)
    # 统一缩放到视频尺寸，保证首帧与成片画幅一致
    await asyncio.to_thread(_fit_in_place, Path(saved), ratio)
    return storage.rel_static_url(Path(saved))


def _fit_in_place(path: Path, ratio: str) -> None:
    """把图片等比裁满到视频尺寸并覆盖保存。"""
    with Image.open(path) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGB")
    ImageOps.fit(img, frame_size(ratio), method=Image.Resampling.LANCZOS).save(path, format="JPEG", quality=92)


async def ensure_motion_frame(project_id: int, shot_id: int, *, ratio: str) -> str:
    """动态镜头提交视频前保证有满幅首帧；已有则直接返回，否则扩图并写回分镜图。"""
    from app.database import AsyncSessionLocal
    from app.models import Shot

    async with AsyncSessionLocal() as db:
        shot = await db.get(Shot, shot_id)
        if shot is None:
            raise ValueError("分镜不存在")
        if is_motion_frame(shot.image_url):
            return str(shot.image_url)
        snapshot = type("ShotRef", (), {"id": shot.id, "shot_no": shot.shot_no, "image_url": shot.image_url})()
    url = await outpaint_frame(project_id, snapshot, ratio=ratio)
    async with AsyncSessionLocal() as db:
        shot = await db.get(Shot, shot_id)
        if shot is not None:
            shot.image_url = url
            shot.image_ark_url = None
            await db.commit()
    logger.info("daihuo motion frame ready project=%s shot=%s", project_id, shot_id)
    return url


def video_duration(shot_duration: float) -> int:
    """视频请求时长：覆盖口播并留余量，限制在模型下限与 12 秒之间（合成时再裁到口播长度）。"""
    s = get_settings()
    want = math.ceil(float(shot_duration or 4) + 0.5)
    return int(max(s.seedance_duration_min, min(12, want)))


def video_kwargs(*, image_url: str, prompt: str, duration: float) -> dict[str, Any]:
    """带货动态镜头提交 Seedance 的参数：纯文本提示词、首帧角色、可内嵌本地图、不串上一镜尾帧。"""
    s = get_settings()
    role = (s.daihuo_video_image_role or "first_frame").strip()
    return {
        "image_url": image_url,
        "prompt": prompt,
        "duration": video_duration(duration),
        "image_role": "first_frame" if role == "first_frame" else None,
        "inline_refs": bool(s.daihuo_video_inline_refs),
        "prompt_as_json": False,
        "extra_image_urls": None,
    }
