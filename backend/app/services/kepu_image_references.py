"""短视频画风与主体参考：每镜固定画风图，云端只接收可访问图片。"""
from __future__ import annotations

from typing import Any
from ipaddress import ip_address
from urllib.parse import urlsplit

from app.config import get_settings
from app.services import storage
from app.services.style_lock import seedream_ref_urls
from app.services.tokenfree_image import is_tokenfree_image_url


def _public_reference(url: str) -> str | None:
    """过滤私网、回环与需要 TokenFree 鉴权的图片地址。"""
    if is_tokenfree_image_url(url) or not seedream_ref_urls(url, limit=1):
        return None
    host = urlsplit(url).hostname or ""
    try:
        if not ip_address(host).is_global:
            return None
    except ValueError:
        if host.endswith(".localhost"):
            return None
    return url


def public_image_reference(raw: str | None) -> str | None:
    """本地图片同步发布或使用站点公网地址，拒绝鉴权产物与越界路径。"""
    url = str(raw or "").strip()
    if not url or is_tokenfree_image_url(url):
        return None
    if public := _public_reference(url):
        return public
    if not storage.is_local_static_url(url):
        return None
    local = storage.local_path_from_url(url)
    if not local or not local.resolve().is_relative_to(storage.STATIC_ROOT.resolve()) or not local.is_file():
        return None
    published = str(storage.republish_url(url, sync=True) or "")
    return _public_reference(published) or _public_reference(storage.to_public_url(url))


def project_image_reference_inputs(project: Any) -> tuple[list[str], list[str]]:
    """只读取主体与画风引用槽位；预占计费时不上传文件。"""
    template = getattr(project, "template", None)
    config = getattr(template, "seedream_config", None) or {}
    config = config if isinstance(config, dict) else {}
    styles = config.get("style_ref_urls") or []
    styles = [url.strip() for url in styles if isinstance(url, str) and url.strip()] if isinstance(styles, list) else []
    legacy_refs = config.get("ref_images") or []
    legacy_refs = [url.strip() for url in legacy_refs if isinstance(url, str) and url.strip()] if isinstance(legacy_refs, list) else []
    cover = str(getattr(template, "preview_cover", None) or "").strip()
    if not cover:
        from app.services.creation_templates import CREATION_TEMPLATE_IDS

        template_id = getattr(template, "id", None)
        if template_id in CREATION_TEMPLATE_IDS:
            cover = f"/static/templates/covers/{template_id}.png"
    if not styles and cover:
        styles = [cover]
    subject = str(getattr(project, "ref_image_url", None) or "").strip()
    subjects = list(dict.fromkeys(([subject] if subject else []) + legacy_refs))[:2]
    return subjects, styles


def project_image_references(project: Any) -> tuple[list[str], list[str]]:
    """解析用户主体图与独立画风图；真实生成不可静默退回纯文字模式。"""
    subjects, styles = project_image_reference_inputs(project)
    settings = get_settings()
    mocked = bool(settings.ark_mock or settings.local_media_mock)

    def resolve(url: str) -> str | None:
        """离线模拟不上传；真实任务仅传公网图。"""
        if mocked and url.startswith("/static/") and ".." not in url.split("/"):
            return url
        return public_image_reference(url)

    subject_refs = [value for url in subjects if (value := resolve(url))]
    style_refs = []
    for url in styles:
        if value := resolve(url):
            style_refs.append(value)
            break
    if subjects and not subject_refs:
        raise ValueError("主体参考图无法访问，请重新上传参考图，或检查公网访问地址和 OSS 配置")
    if styles and not style_refs:
        raise ValueError("画风参考图无法访问，请检查公网访问地址或 OSS 配置后重试")
    return subject_refs, style_refs
