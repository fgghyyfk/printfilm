"""漫剧分集成片：统一画幅重编码后拼接（兼容各镜 HEVC 参数不一致）。"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from uuid import uuid4

from app.models_drama import DramaEpisode, DramaProject
from app.services import storage as storage_svc
from app.services.drama.output_settings import resolve_episode_video_output, target_pixel_size
from app.services.ffmpeg_compose import _run, _scale_pad, _which

logger = logging.getLogger("app.drama.episode_compose")


# 将单镜规范到目标分辨率（H.264 + AAC），便于后续 stream copy 拼接
def _normalize_clip(src: Path, dest: Path, width: int, height: int) -> None:
    ffmpeg = _which("ffmpeg")
    vf = _scale_pad(width, height)
    _run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(src),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )


# 拼接已规范化的片段（stream copy）
def _concat_normalized(paths: list[Path], output: Path) -> None:
    ffmpeg = _which("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="drama_ep_concat_") as tmp:
        tmp_path = Path(tmp)
        concat_list = tmp_path / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in paths),
            encoding="utf-8",
        )
        merged = tmp_path / "merged.mp4"
        _run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(merged),
            ]
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(merged.read_bytes())


# 确保远程/本地视频落到可 ffmpeg 读取的本地路径
async def _ensure_clip_local(url: str, cache_dir: Path, index: int) -> Path:
    local = storage_svc.local_path_from_url(url)
    if local is not None:
        if not local.exists():
            await storage_svc.ensure_local_media(url, local)
        if local.exists() and local.stat().st_size > 0:
            return local
    dest = cache_dir / f"src_{index:03d}.mp4"
    await storage_svc.ensure_local_media(url, dest)
    if not dest.exists() or dest.stat().st_size <= 0:
        raise RuntimeError(f"无法下载分镜视频：{url[:120]}")
    return dest


# 入队时固定有权限的镜头、播放顺序及输出规格，不让排队后的编辑改变成片。
def episode_compose_snapshot(
    episode: DramaEpisode, project: DramaProject, fragment_ids: list[int] | None,
) -> dict:
    frags = sorted(episode.fragments or [], key=lambda f: f.sort_order)
    if fragment_ids is not None:
        allow = {int(x) for x in fragment_ids}
        if not allow:
            raise ValueError("本集还没有可拼接的分镜视频")
        if not allow.issubset({f.id for f in frags}):
            raise ValueError("所选分镜不属于本集或已移除，请刷新后重新选择")
        frags = [f for f in frags if f.id in allow]
        if any(not (f.video or "").strip() for f in frags):
            raise ValueError("所选分镜尚无视频，请刷新后重试")
    clips = [{"fragment_id": f.id, "video_url": f.video.strip()} for f in frags if (f.video or "").strip()]
    if not clips:
        raise ValueError("本集还没有可拼接的分镜视频")
    ratio, resolution = resolve_episode_video_output(episode.params, project.params)
    return {"clips": clips, "aspect_ratio": ratio, "resolution": resolution}


# 只使用已验证的任务快照合成；每次执行使用独立路径，不覆盖历史或并发产物。
async def compose_episode_video(
    *, snapshot: dict, project_id: int, episode_id: int, task_id: int,
) -> str:
    clips = snapshot["clips"]
    width, height = target_pixel_size(snapshot["aspect_ratio"], snapshot["resolution"])
    out_dir = storage_svc.GENERATED_ROOT / "drama" / f"p{project_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"episode_{episode_id}_task_{task_id}_{uuid4().hex}.mp4"

    with tempfile.TemporaryDirectory(prefix=".compose_", dir=out_dir) as tmp:
        tmp_path = Path(tmp)
        src_dir = tmp_path / "src"
        norm_dir = tmp_path / "norm"
        src_dir.mkdir()
        norm_dir.mkdir()

        local_srcs: list[Path] = []
        for idx, clip in enumerate(clips):
            local_srcs.append(await _ensure_clip_local(clip["video_url"], src_dir, idx))

        normalized: list[Path] = []
        for idx, src in enumerate(local_srcs):
            dest = norm_dir / f"seg_{idx:03d}.mp4"
            await asyncio.to_thread(_normalize_clip, src, dest, width, height)
            normalized.append(dest)

        await asyncio.to_thread(_concat_normalized, normalized, out_path)

    published = await asyncio.to_thread(storage_svc.publish_local, out_path, sync=True)
    if published and str(published).startswith(("http://", "https://", "/static/")):
        url = str(published)
    else:
        rel = "/static/" + str(out_path.relative_to(storage_svc.STATIC_ROOT)).replace("\\", "/")
        url = storage_svc.to_public_url(rel)
    logger.info(
        "episode compose done episode_id=%s clips=%s size=%sx%s url=%s",
        episode_id,
        len(clips),
        width,
        height,
        url[:120],
    )
    return url
