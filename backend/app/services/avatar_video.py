"""数字人对口型视频上游：自建 GPU 推理服务（deploy/avatar-gpu/server）的客户端。

接口：POST /v1/jobs 提交「照片 + 音频」，GET /v1/jobs/{id} 查状态，GET /v1/jobs/{id}/video 下载成片。
地址、Key、默认引擎在管理后台「系统设置 → 数字人」配置（avatar_* 字段）。
ARK_MOCK=true 且未配服务地址时不请求服务，用 FFmpeg 把照片 + 音频合成静态视频，便于无 GPU 联调。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import get_settings
from app.services.tasks.upstream import observed_request

logger = logging.getLogger(__name__)

ENGINES = ("infinitetalk", "longcat")
MOCK_PREFIX = "mock-avatar-"


@dataclass
class AvatarJobStatus:
    """一次状态查询结果。status：queued | loading | running | succeeded | failed。"""

    status: str
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    queue_position: int | None = None


class AvatarNotConfigured(RuntimeError):
    """后台未配置数字人服务地址。"""


def resolve_engine(engine: str | None) -> str:
    """项目指定的引擎优先，否则用后台默认；非法值回退 infinitetalk。"""
    name = (engine or get_settings().avatar_engine or "infinitetalk").strip().lower()
    return name if name in ENGINES else "infinitetalk"


def is_mock() -> bool:
    """是否走本地模拟：ARK_MOCK=true 且没配数字人服务地址。

    配了地址就始终走真服务，便于本地「文本/配音模拟 + 真 GPU 对口型」联调。
    """
    s = get_settings()
    return bool(s.ark_mock) and not (getattr(s, "avatar_base_url", "") or "").strip()


def _base_and_headers() -> tuple[str, dict[str, str]]:
    """服务地址与鉴权头；未配置时抛 AvatarNotConfigured。"""
    s = get_settings()
    base = (s.avatar_base_url or "").strip().rstrip("/")
    if not base:
        raise AvatarNotConfigured("数字人服务未配置：请在管理后台「系统设置 → 数字人」填写服务地址")
    headers = {"Authorization": f"Bearer {s.avatar_api_key}"} if s.avatar_api_key else {}
    return base, headers


def _detail(resp: httpx.Response) -> str:
    """提取服务返回的错误说明。"""
    try:
        return str(resp.json().get("detail") or resp.text)[:300]
    except ValueError:
        return resp.text[:300]


async def submit(image: Path, audio: Path, *, engine: str, prompt: str = "", resolution: str | None = None) -> str:
    """提交一段「照片 + 音频」，返回服务端任务 id。"""
    if is_mock():
        return MOCK_PREFIX + hashlib.md5(f"{image}{audio}{time.time()}".encode()).hexdigest()[:12]
    base, headers = _base_and_headers()
    data = {"engine": resolve_engine(engine), "prompt": prompt[:500],
            "resolution": resolution or get_settings().avatar_resolution or "480p"}
    timeout = httpx.Timeout(connect=15.0, read=120.0, write=120.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        with image.open("rb") as fi, audio.open("rb") as fa:
            files = {"image": (image.name, fi, "application/octet-stream"),
                     "audio": (audio.name, fa, "application/octet-stream")}
            resp = await observed_request(client, "POST", f"{base}/v1/jobs", provider="avatar",
                                          headers=headers, data=data, files=files)
    if resp.status_code >= 400:
        raise RuntimeError(f"数字人提交失败（HTTP {resp.status_code}）：{_detail(resp)}")
    return str(resp.json()["id"])


async def fetch_once(job_id: str) -> AvatarJobStatus:
    """查询一次任务状态；网络抖动或 5xx 视为仍在运行，由调用方继续轮询。"""
    if job_id.startswith(MOCK_PREFIX):
        return AvatarJobStatus(status="succeeded", progress=1.0, message="模拟完成")
    base, headers = _base_and_headers()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=30.0)) as client:
            resp = await client.get(f"{base}/v1/jobs/{job_id}", headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("avatar poll network error job=%s: %s", job_id, exc)
        return AvatarJobStatus(status="running", message="数字人服务暂时连不上，稍后重试")
    if resp.status_code == 404:
        return AvatarJobStatus(status="failed", error="数字人服务找不到该任务（可能服务重启或已过期）")
    if resp.status_code >= 500 or resp.status_code == 429:
        return AvatarJobStatus(status="running", message=f"数字人服务繁忙（HTTP {resp.status_code}）")
    if resp.status_code >= 400:
        return AvatarJobStatus(status="failed", error=f"查询失败（HTTP {resp.status_code}）：{_detail(resp)}")
    d = resp.json()
    status = str(d.get("status") or "running")
    if status == "cancelled":
        status = "failed"
    return AvatarJobStatus(status=status, progress=float(d.get("progress") or 0), message=str(d.get("message") or ""),
                           error=d.get("error"), queue_position=d.get("queue_position"))


async def download(job_id: str, dest: Path, *, image: Path, audio: Path) -> Path:
    """下载成片到 dest。模拟任务用传入的照片 + 音频合成静态视频（真实任务忽略这两个参数）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if job_id.startswith(MOCK_PREFIX):
        await _mock_video(image, audio, dest)
        return dest
    base, headers = _base_and_headers()
    tmp = dest.with_name(dest.name + ".part")
    timeout = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", f"{base}/v1/jobs/{job_id}/video", headers=headers) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise RuntimeError(f"数字人成片下载失败（HTTP {resp.status_code}）：{_detail(resp)}")
            with tmp.open("wb") as f:
                async for chunk in resp.aiter_bytes(1 << 20):
                    f.write(chunk)
    tmp.replace(dest)
    return dest


async def wait(job_id: str, *, on_progress=None) -> AvatarJobStatus:
    """阻塞轮询直到成功或失败；超过 avatar_poll_timeout 抛 TimeoutError。

    on_progress(status) 可选，每次查询后回调（用于更新项目进度文案）。
    """
    s = get_settings()
    deadline = time.monotonic() + float(s.avatar_poll_timeout)
    interval = max(2.0, float(s.avatar_poll_interval)) if not job_id.startswith(MOCK_PREFIX) else 0.0
    while True:
        st = await fetch_once(job_id)
        if on_progress:
            await on_progress(st)
        if st.status in ("succeeded", "failed"):
            return st
        if time.monotonic() > deadline:
            raise TimeoutError(f"数字人生成超时（超过 {s.avatar_poll_timeout / 3600:.1f} 小时）")
        await asyncio.sleep(interval)


async def cancel(job_id: str) -> None:
    """尽力取消（只对排队中的任务有效；运行中的会跑完，结果丢弃）。"""
    if job_id.startswith(MOCK_PREFIX):
        return
    try:
        base, headers = _base_and_headers()
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            await client.delete(f"{base}/v1/jobs/{job_id}", headers=headers)
    except Exception as exc:  # noqa: BLE001
        logger.info("avatar cancel ignored job=%s: %s", job_id, exc)


async def health() -> dict:
    """查询服务健康状态（后台「测试连接」用）。"""
    base, headers = _base_and_headers()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(f"{base}/v1/health", headers=headers)
    resp.raise_for_status()
    return resp.json()


async def _mock_video(image: Path, audio: Path, dest: Path) -> None:
    """模拟成片：照片静帧 + 原音频，竖屏 720 宽。"""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(image), "-i", str(audio),
           "-vf", "scale=720:-2,format=yuv420p", "-c:v", "libx264", "-tune", "stillimage", "-r", "25",
           "-c:a", "aac", "-shortest", str(dest)]
    proc = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"模拟数字人视频失败：{err.decode(errors='ignore')[-200:]}")
