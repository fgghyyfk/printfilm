"""引擎工作进程公共协议：从 stdin 逐行读任务 JSON，向 fd 3 写结果 JSON。

工作进程启动后先加载模型（十几分钟），完成后发 {"type": "ready"}；之后每个任务：
  输入：{"id", "image", "audio", "prompt", "resolution", "out"}（路径均为本机绝对路径）
  输出：{"type": "progress", "id", "progress": 0~1, "message"} 若干条
        {"type": "done", "id", "video": out} 或 {"type": "error", "id", "error"}
模型库自己的 print/日志都走 stdout/stderr（由服务重定向到日志文件），不会混进结果通道。
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Callable

RESULT_FD = 3
_chan = None


def emit(msg: dict) -> None:
    """向结果通道写一条 JSON 消息并立即刷新。"""
    global _chan
    if _chan is None:
        _chan = os.fdopen(RESULT_FD, "w", buffering=1, encoding="utf-8")
    _chan.write(json.dumps(msg, ensure_ascii=False) + "\n")
    _chan.flush()


def serve(handle: Callable[[dict, Callable[[float, str], None]], None]) -> None:
    """主循环：逐行读任务，调用 handle(job, report)；单个任务失败不影响后续任务。

    handle 负责把成片写到 job["out"]；report(progress, message) 用于上报进度。
    """
    emit({"type": "ready"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        job = json.loads(line)
        jid = job["id"]

        def report(p: float, message: str = "", _jid: str = jid) -> None:
            emit({"type": "progress", "id": _jid, "progress": round(float(p), 3), "message": message})

        try:
            handle(job, report)
            if not os.path.exists(job["out"]):
                raise RuntimeError("生成结束但没有输出文件")
            emit({"type": "done", "id": jid, "video": job["out"]})
        except Exception as exc:  # noqa: BLE001 - 任何异常都回报给服务，进程继续服务下一个任务
            traceback.print_exc()
            emit({"type": "error", "id": jid, "error": f"{type(exc).__name__}: {exc}"[:500]})
        finally:
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
