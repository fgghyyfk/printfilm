"""跨 kind 的结果裂变：用户隔离、独立引用副本和共用任务计费。"""
import asyncio
from pathlib import Path

from app.schemas_ecom_jobs import EcomVariantsParams
from app.services import storage
from app.services.ecom_jobs import kinds, service


# 优先读取本地引用；仅真实模式允许从存储恢复远程图片，mock 完全不联网。
async def reference_bytes(url: str, path: Path) -> bytes:
    from app.config import get_settings

    local = path if path.is_file() else storage.local_path_from_url(url)
    if local is None or not local.is_file():
        if get_settings().ark_mock:
            raise ValueError("图片无法识别，请换一张")
        local = await storage.ensure_local_media(url, path)
    if local.stat().st_size > service.MAX_UPLOAD:
        # 生成的 4K PNG 可能超过上传限额，重新编码后再按输入规则验证。
        return await asyncio.to_thread(_compress_reference, local)
    return await asyncio.to_thread(local.read_bytes)


# 大尺寸成图转为高质量 JPEG 参考，避免裂变被上传 PNG 大小限制阻断。
def _compress_reference(path: Path) -> bytes:
    import io
    from PIL import Image

    with Image.open(path) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
        return buffer.getvalue()


# 从当前用户的成功图片派生新同类任务，旧任务和旧结果保持不变。
async def create_variants(db, user, job_id: int, idx: int, *, count: int):
    count = EcomVariantsParams(count=count).count
    job = await service.get_job(db, user, job_id)
    item = next((item for item in job.items if item.idx == idx), None)
    if item is None:
        raise LookupError("图片不存在")
    if item.status != "succeeded" or not item.result_url:
        raise ValueError("还没有生成好的图片")
    inputs = [ref for ref in item.inputs if ref["role"] != "source"]
    product_count = sum(ref["role"] == "product" for ref in inputs)
    params = kinds.validate(job.kind, product_count, job.params)
    if job.kind == "buyer_show":
        params["count"] = count
    # 结果图有本地副本，即使 result_url 已回填为 OSS 仍优先复用。
    directory = service.job_dir(user.id, job.id)
    local = storage.local_path_from_url(item.result_url)
    if local is None or not local.is_file():
        candidates = list(directory.glob(f"result_{idx}_*.png"))
        local = max(candidates, key=lambda path: path.stat().st_mtime) if candidates else directory / f"variant_source_{idx}.png"
    uploads = []
    for ref in inputs + [{"role": "source", "url": item.result_url, "path": str(local)}]:
        data = await reference_bytes(ref["url"], Path(ref["path"]))
        uploads.append((ref["role"], data, "reference.png"))
    return await service.create_prepared_job(db, user, kind=job.kind, params=params, title=job.title,
                                             uploads=uploads, count=count, combined=True)
