"""形象媒体版本与视频输入快照，独立于名称、画布和其它资产参数。"""
from uuid import uuid4


def bump_asset_image_revision(asset) -> None:
    """真实媒体替换成功后旋转不透明版本，同路径覆盖也产生新版本。"""
    asset.image_revision = uuid4().hex


def asset_image_identity(asset) -> dict:
    """优先媒体版本；旧资产用规范本地标识兼容 static/OSS 同一文件别名。"""
    from app.services import storage

    url = str(asset.cover or asset.url or "").strip()
    path = storage.local_path_from_url(url)
    if path is not None:
        try:
            url = "/static/" + path.relative_to(storage.STATIC_ROOT).as_posix()
        except ValueError:
            pass
    return {"asset_id": asset.id, "revision": str(getattr(asset, "image_revision", None) or ""), "media": url}


def fragment_reference_snapshot(assets: list, image_ids: list[int]) -> list[dict]:
    """只记录最终实际提交的图像资产，保留参考图顺序。"""
    by_id = {asset.id: asset for asset in assets}
    return [asset_image_identity(by_id[aid]) for aid in image_ids if aid in by_id]
