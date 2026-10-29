"""数字人 GPU 推理服务：把 LongCat / InfiniteTalk 包成「提交任务 → 查询状态 → 下载视频」的 HTTP 接口。

- 单卡串行：同一时间只跑一个任务，其余排队（FIFO）
- 模型常驻：引擎工作进程加载一次后一直保留；任务换引擎时才关掉旧进程、拉起新进程（24GB 单卡放不下两套模型）
- 任务落盘：jobs/<id>/job.json，服务重启后已完成的任务仍可查询下载，未完成的标记失败

接口（若设置了 AVATAR_API_KEY，需带 Authorization: Bearer <key>）：
  POST   /v1/jobs              multipart：image、audio 文件；engine、prompt、resolution 表单字段
  GET    /v1/jobs/{id}         状态：queued / loading / running / succeeded / failed
  GET    /v1/jobs/{id}/video   下载成片 mp4
  DELETE /v1/jobs/{id}         取消排队中的任务（运行中的无法中断）
  GET    /v1/health            服务、引擎与队列状态

启动：见 start.sh（需在 WSL / Linux 上运行，依赖 bench 安装好的 conda 环境与权重）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

HERE = Path(__file__).resolve().parent
WORK_DIR = Path(os.environ.get("WORK_DIR", "/root/avatar-bench"))
CONDA_HOME = Path(os.environ.get("CONDA_HOME", str(Path.home() / "miniforge3")))
DATA_DIR = Path(os.environ.get("AVATAR_DATA_DIR", str(HERE / "data")))
API_KEY = os.environ.get("AVATAR_API_KEY", "")
# 任务成片保留时长（秒），过期自动清理
KEEP_SECONDS = int(os.environ.get("AVATAR_KEEP_SECONDS", str(3 * 86400)))

# 引擎 → (conda 环境, 仓库目录, 工作进程脚本)
ENGINES = {
    "infinitetalk": ("multitalk", WORK_DIR / "InfiniteTalk", HERE / "worker_infinitetalk.py"),
    "longcat": ("longcat-video", WORK_DIR / "LongCat-Video", HERE / "worker_longcat.py"),
}
DEFAULT_ENGINE = os.environ.get("AVATAR_DEFAULT_ENGINE", "infinitetalk")
TERMINAL = {"succeeded", "failed", "cancelled"}

@asynccontextmanager
async def _lifespan(_app):
    """启动时恢复历史任务并启动调度线程。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _load_existing()
    threading.Thread(target=_scheduler, daemon=True).start()
    yield


app = FastAPI(title="avatar-gpu", lifespan=_lifespan)
_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_queue: deque[str] = deque()
_wake = threading.Event()


# ---------- 任务存储 ----------

def _job_dir(jid: str) -> Path:
    """任务目录：输入素材、成片与 job.json 都放这里。"""
    return DATA_DIR / "jobs" / jid


def _save(job: dict) -> None:
    """把任务状态写盘（调用方持有 _lock）。"""
    path = _job_dir(job["id"]) / "job.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _update(jid: str, **fields) -> None:
    """更新任务字段并落盘。"""
    with _lock:
        job = _jobs.get(jid)
        if not job:
            return
        job.update(fields, updated_at=time.time())
        _save(job)


def _load_existing() -> None:
    """启动时恢复历史任务：未完成的一律标记失败（工作进程已不在）。"""
    root = DATA_DIR / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    for p in root.glob("*/job.json"):
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if job.get("status") not in TERMINAL:
            job.update(status="failed", error="推理服务重启，任务中断，请重新提交")
            p.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        _jobs[job["id"]] = job


def _cleanup_expired() -> None:
    """删除过期的已结束任务目录。"""
    cutoff = time.time() - KEEP_SECONDS
    with _lock:
        expired = [j for j, job in _jobs.items()
                   if job.get("status") in TERMINAL and job.get("updated_at", 0) < cutoff]
        for jid in expired:
            _jobs.pop(jid, None)
            shutil.rmtree(_job_dir(jid), ignore_errors=True)


# ---------- 引擎工作进程 ----------

class Worker:
    """一个引擎的常驻工作进程：stdin 发任务，fd 3 收结果（协议见 worker_common.py）。"""

    def __init__(self, engine: str):
        env_name, repo, script = ENGINES[engine]
        self.engine = engine
        self.log_path = DATA_DIR / f"worker_{engine}.log"
        r, w = os.pipe()
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        cmd = (f'source "{CONDA_HOME}/etc/profile.d/conda.sh" && conda activate {env_name} '
               f'&& cd "{repo}" && exec python "{script}"')
        self._log = open(self.log_path, "ab")
        # bash 的 exec 会保留 fd 3：结果通道
        self.proc = subprocess.Popen(
            ["bash", "-c", f"exec 3>&{w}; {cmd}"], stdin=subprocess.PIPE, stdout=self._log,
            stderr=subprocess.STDOUT, pass_fds=(w,), env=env, start_new_session=True,
        )
        os.close(w)
        self._chan = os.fdopen(r, "r", encoding="utf-8")
        self.ready = False

    def read(self) -> dict | None:
        """阻塞读一条结果消息；进程退出时返回 None。"""
        line = self._chan.readline()
        return json.loads(line) if line else None

    def send(self, job: dict) -> None:
        """发送一个任务。"""
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(job, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def alive(self) -> bool:
        """进程是否还在运行。"""
        return self.proc.poll() is None

    def stop(self) -> None:
        """结束工作进程（释放显存与内存）。"""
        try:
            os.killpg(self.proc.pid, 15)
            self.proc.wait(timeout=60)
        except Exception:  # noqa: BLE001
            try:
                os.killpg(self.proc.pid, 9)
            except Exception:  # noqa: BLE001
                pass
        self._log.close()


_worker: Worker | None = None


def _log_tail(path: Path, n: int = 15) -> str:
    """取工作进程日志末尾若干行，用于失败原因。"""
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="ignore").splitlines()[-n:])
    except OSError:
        return ""


def _ensure_worker(engine: str, jid: str | None = None) -> Worker:
    """确保目标引擎的工作进程已就绪；需要换引擎时先关旧进程。加载失败抛 RuntimeError。"""
    global _worker
    if _worker and (_worker.engine != engine or not _worker.alive()):
        _worker.stop()
        _worker = None
    if _worker is None:
        if jid:
            _update(jid, status="loading", message=f"加载 {engine} 模型（首次约 15 分钟）")
        _worker = Worker(engine)
    while not _worker.ready:
        msg = _worker.read()
        if msg is None:
            tail = _log_tail(_worker.log_path)
            _worker.stop()
            _worker = None
            raise RuntimeError(f"{engine} 模型加载失败：{tail[-400:]}")
        if msg.get("type") == "ready":
            _worker.ready = True
    return _worker


def _run_job(jid: str) -> None:
    """执行单个任务直到结束（成功 / 失败）。"""
    global _worker
    job = _jobs[jid]
    try:
        worker = _ensure_worker(job["engine"], jid)
    except Exception as exc:  # noqa: BLE001
        _update(jid, status="failed", error=str(exc)[:600])
        return
    started = time.time()
    _update(jid, status="running", started_at=started, message="排队结束，开始生成")
    d = _job_dir(jid)
    worker.send({"id": jid, "image": str(d / job["image"]), "audio": str(d / job["audio"]),
                 "prompt": job.get("prompt") or "", "resolution": job.get("resolution") or "480p",
                 "out": str(d / "output.mp4")})
    while True:
        msg = worker.read()
        if msg is None:
            tail = _log_tail(worker.log_path)
            worker.stop()
            _worker = None
            _update(jid, status="failed", error=f"工作进程异常退出：{tail[-400:]}")
            return
        if msg.get("id") != jid:
            continue
        kind = msg.get("type")
        if kind == "progress":
            _update(jid, progress=msg.get("progress", 0), message=msg.get("message", ""))
        elif kind == "done":
            _update(jid, status="succeeded", progress=1.0, message="完成",
                    elapsed_seconds=round(time.time() - started, 1))
            return
        elif kind == "error":
            _update(jid, status="failed", error=msg.get("error") or "生成失败",
                    elapsed_seconds=round(time.time() - started, 1))
            return


def _scheduler() -> None:
    """后台线程：串行取队列任务执行；空闲时顺带清理过期任务。

    只有这一个线程读写工作进程。AVATAR_PRELOAD=1 时启动即加载默认引擎，避免第一个任务多等十几分钟。
    """
    if os.environ.get("AVATAR_PRELOAD") == "1":
        try:
            _ensure_worker(DEFAULT_ENGINE)
        except Exception as exc:  # noqa: BLE001
            print(f"预加载失败：{exc}", flush=True)
    while True:
        _wake.wait(timeout=600)
        _wake.clear()
        while True:
            with _lock:
                jid = _queue.popleft() if _queue else None
            if jid is None:
                break
            if _jobs.get(jid, {}).get("status") == "queued":
                _run_job(jid)
        _cleanup_expired()


# ---------- HTTP 接口 ----------

def _auth(authorization: str = Header(default="")) -> None:
    """校验 Bearer Key（未配置 AVATAR_API_KEY 时不校验，仅限内网）。"""
    if API_KEY and authorization != f"Bearer {API_KEY}":
        raise HTTPException(401, "API Key 无效")


def _public(job: dict) -> dict:
    """对外返回的任务字段（含排队位置）。"""
    out = {k: job.get(k) for k in ("id", "engine", "status", "progress", "message", "error",
                                     "created_at", "started_at", "elapsed_seconds", "audio_seconds")}
    if job.get("status") == "queued":
        with _lock:
            out["queue_position"] = list(_queue).index(job["id"]) + 1 if job["id"] in _queue else None
    return out


def _probe_seconds(path: Path) -> float | None:
    """ffprobe 读音频时长。"""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(path)], capture_output=True, text=True, timeout=30)
        return round(float(r.stdout.strip()), 2)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


@app.post("/v1/jobs", dependencies=[Depends(_auth)])
async def create_job(image: UploadFile = File(...), audio: UploadFile = File(...),
                     engine: str = Form(DEFAULT_ENGINE), prompt: str = Form(""),
                     resolution: str = Form("480p")) -> dict:
    """提交任务：保存照片与音频后入队。"""
    if engine not in ENGINES:
        raise HTTPException(400, f"不支持的引擎：{engine}")
    if resolution not in ("480p", "720p"):
        raise HTTPException(400, "分辨率只支持 480p / 720p")
    jid = uuid.uuid4().hex[:16]
    d = _job_dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    img_name = "image" + (Path(image.filename or "").suffix.lower() or ".png")
    aud_name = "audio" + (Path(audio.filename or "").suffix.lower() or ".wav")
    (d / img_name).write_bytes(await image.read())
    (d / aud_name).write_bytes(await audio.read())
    seconds = _probe_seconds(d / aud_name)
    if not seconds:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, "音频无法解析")
    now = time.time()
    job = {"id": jid, "engine": engine, "status": "queued", "progress": 0.0, "message": "排队中",
           "error": None, "prompt": prompt[:500], "resolution": resolution, "image": img_name,
           "audio": aud_name, "audio_seconds": seconds, "created_at": now, "updated_at": now}
    with _lock:
        _jobs[jid] = job
        _queue.append(jid)
        _save(job)
    _wake.set()
    return _public(job)


@app.get("/v1/jobs/{jid}", dependencies=[Depends(_auth)])
def get_job(jid: str) -> dict:
    """查询任务状态。"""
    job = _jobs.get(jid)
    if not job:
        raise HTTPException(404, "任务不存在或已过期")
    return _public(job)


@app.get("/v1/jobs/{jid}/video", dependencies=[Depends(_auth)])
def get_video(jid: str) -> FileResponse:
    """下载成片。"""
    job = _jobs.get(jid)
    path = _job_dir(jid) / "output.mp4"
    if not job or job.get("status") != "succeeded" or not path.exists():
        raise HTTPException(404, "成片不存在")
    return FileResponse(path, media_type="video/mp4", filename=f"{jid}.mp4")


@app.delete("/v1/jobs/{jid}", dependencies=[Depends(_auth)])
def cancel_job(jid: str) -> dict:
    """取消排队中的任务；已开始的任务无法中断。"""
    with _lock:
        job = _jobs.get(jid)
        if not job:
            raise HTTPException(404, "任务不存在")
        if job["status"] == "queued":
            if jid in _queue:
                _queue.remove(jid)
            job.update(status="cancelled", updated_at=time.time())
            _save(job)
    return _public(job)


@app.get("/v1/health")
def health() -> dict:
    """服务与引擎状态（不需要鉴权，不含敏感信息）。"""
    with _lock:
        queued = len(_queue)
        running = [j for j, job in _jobs.items() if job.get("status") in ("loading", "running")]
    return {"ok": True, "engines": list(ENGINES), "default_engine": DEFAULT_ENGINE,
            "active_engine": _worker.engine if _worker else None,
            "worker_ready": bool(_worker and _worker.ready), "queued": queued, "running": running}

