"""跨工具成功图入口，只接受当前用户任务标识，不接受客户端任意媒体 URL。"""
from pathlib import Path

from app.config import get_settings
from app.services import storage
from app.services.ecom_jobs.jobs import job_dir


# 校验来源归属与成功状态，优先本地结果；mock 不下载远程素材。
async def result_path(db, user, job_id: int, idx: int) -> Path:
    from app.services.ecom_jobs.service import get_job

    job = await get_job(db, user, job_id)
    item = next((item for item in job.items if item.idx == idx), None)
    if item is None:
        raise ValueError("图片不存在")
    if item.status != "succeeded" or not item.result_url:
        raise ValueError("还没有生成好的图片")
    return await item_result_path(user, job_id, item)


# 解析已由调用方校验的成功图片，不查询或提交事务，适合父任务行锁内使用。
async def item_result_path(user, job_id: int, item) -> Path:
    idx = item.idx
    local = storage.local_path_from_url(item.result_url)
    if local is not None and local.is_file():
        return local
    directory = job_dir(user.id, job_id)
    candidates = list(directory.glob(f"result_{idx}_*.png"))
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    if get_settings().ark_mock:
        raise ValueError("图片无法识别，请换一张")
    return await storage.ensure_local_media(item.result_url, directory / f"next_source_{idx}.png")


# 沿用裂变的参考图编码策略，交给目标工具原有上传校验与任务流程。
async def source_uploads(db, user, job_id: int, idx: int) -> list[tuple[bytes, str]]:
    from app.services.ecom_jobs.variants import reference_bytes

    path = await result_path(db, user, job_id, idx)
    return [(await reference_bytes("", path), "result.png")]
