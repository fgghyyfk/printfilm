"""共用后台 OSS 配置，独立私有对象；不复用公开上传函数。"""
from functools import lru_cache, wraps
import json
from pathlib import Path
import time

from fastapi import HTTPException
from app.config import get_settings

from .config import get_marketplace_settings

# 已核验客户端及核验时刻；一分钟后重新检查 Bucket 权限。
_verified_at: float = 0
_verified_client = None


def unavailable() -> HTTPException:
    """统一文件服务缺失或不可用错误，避免泄漏凭证。"""
    return HTTPException(503, detail={"code": "MARKETPLACE_STORAGE_UNAVAILABLE", "message": "文件服务暂未配置或不可用"})


def configured() -> bool:
    """仅检查配置完整性，不联网、不修改云资源。"""
    s = get_settings()
    return bool(s.oss_enabled and s.oss_endpoint and s.oss_bucket and s.oss_access_key_id and s.oss_access_key_secret)


def transport(operation):
    """云端操作失败统一返回503，不向业务响应暴露SDK或凭证错误。"""
    @wraps(operation)
    def guarded(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            raise unavailable() from exc
    return guarded


def _client():
    """读取后台生效配置；保存或清除凭证后立即切换客户端。"""
    s = get_settings()
    if not configured():
        raise unavailable()
    return _shared_client(s.oss_endpoint, s.oss_bucket, s.oss_access_key_id, s.oss_access_key_secret)


@lru_cache(maxsize=1)
def _shared_client(endpoint: str, bucket_name: str, key_id: str, key_secret: str):
    """按完整配置缓存，禁止混用不同配置的凭证和 Bucket。"""
    import oss2
    auth = oss2.Auth(key_id, key_secret)
    endpoint = endpoint if "://" in endpoint else f"https://{endpoint}"
    if not endpoint.startswith("https://"):
        raise unavailable()
    return oss2.Bucket(auth, endpoint, bucket_name, connect_timeout=15)


def bucket():
    """检查私有 ACL 和禁止匿名读取的 Bucket policy，失败关闭。"""
    global _verified_at, _verified_client
    try:
        client = _client()
        if client is not _verified_client or time.monotonic() - _verified_at > 60:
            if client.get_bucket_acl().acl != "private":
                raise unavailable()
            try:
                policy = json.loads(client.get_bucket_policy().policy)
                statements = policy.get("Statement", [])
                if isinstance(statements, dict):
                    statements = [statements]
                for statement in statements:
                    if statement.get("Effect", "").lower() != "allow":
                        continue
                    principal = statement.get("Principal", "")
                    # 下游不接受任何匿名授权策略，避免条件或资源规则被误判。
                    if "*" in json.dumps(principal):
                        raise unavailable()
            except Exception as exc:
                # Aliyun returns 404/NoSuchBucketPolicy when no optional bucket
                # policy has ever been created.  A private ACL is sufficient for
                # the marketplace, so treat that absence as an empty policy;
                # genuine permission/network failures still disable storage.
                details = getattr(exc, "details", {}) or {}
                code = str(getattr(exc, "code", "") or details.get("Code", "") or "")
                if code != "NoSuchBucketPolicy" and "NoSuchBucketPolicy" not in str(exc):
                    raise
            _verified_at = time.monotonic()
            _verified_client = client
        return client
    except HTTPException:
        raise
    except Exception as exc:
        raise unavailable() from exc


@transport
def initiate(key: str, content_type: str) -> str:
    """初始化私有 multipart 对象。"""
    return bucket().init_multipart_upload(key, headers={"Content-Type": content_type, "x-oss-object-acl": "private"}).upload_id


@transport
def part(key: str, remote_id: str, number: int, data: bytes) -> str:
    """只转发单个受限大小分片。"""
    return bucket().upload_part(key, remote_id, number, data).etag


@transport
def complete(key: str, remote_id: str, parts: dict) -> None:
    """按分片序号完成上传；对上传已完成但事务重试的情况保持幂等。"""
    from oss2.models import PartInfo
    client = bucket()
    try:
        client.complete_multipart_upload(key, remote_id, [PartInfo(int(n), item["etag"]) for n, item in sorted(parts.items(), key=lambda x: int(x[0]))])
    except Exception as exc:
        if getattr(exc, "code", "") != "NoSuchUpload" or not client.object_exists(key):
            raise


@transport
def abort(key: str, remote_id: str) -> None:
    """清理未完成上传；重复取消不报错。"""
    try:
        bucket().abort_multipart_upload(key, remote_id)
    except Exception as exc:
        if getattr(exc, "code", "") != "NoSuchUpload":
            raise


@transport
def head(key: str) -> int:
    """取得 OSS 真实字节数进行完成校验。"""
    return int(bucket().head_object(key).content_length)


@transport
def download(key: str, path: Path) -> None:
    """下载到非公开临时工作目录供检测和 FFmpeg 使用。"""
    bucket().get_object_to_file(key, str(path))


@transport
def read(key: str) -> bytes:
    """读取短时播放清单；只允许服务端在鉴权后读取，不向业务返回 OSS 地址。"""
    return bucket().get_object(key).read()


@transport
def upload(key: str, path: Path, content_type: str) -> None:
    """处理结果成功写入私有 OSS 后才允许业务置为 ready。"""
    bucket().put_object_from_file(key, str(path), headers={"Content-Type": content_type, "x-oss-object-acl": "private"})


@transport
def copy_private(source_key: str, dest_key: str, content_type: str) -> int:
    """同桶复制到市场前缀，不把对象下载到浏览器。"""
    client = bucket()
    client.copy_object(client.bucket_name, source_key, dest_key, headers={"Content-Type": content_type, "x-oss-object-acl": "private"})
    return int(client.head_object(dest_key).content_length)


@transport
def discard_generated(key: str) -> None:
    """只清理本次同步生成且未绑定的精确 UUID 对象，绝不删除上游媒体。"""
    import re
    if not re.fullmatch(r"marketplace/series/u\d+/\d+/(?:cover-)?[0-9a-f]{32}\.(?:mp4|png)", key):
        raise ValueError("拒绝清理非市场同步对象")
    bucket().delete_object(key)


def source_metadata(key: str) -> dict:
    """读取上游对象版本；只供服务端的漫剧快照使用。"""
    try:
        result = bucket().head_object(key)
        etag = str(result.etag or "").strip('"')
        if not etag:
            raise ValueError("MARKETPLACE_SOURCE_NOT_READY:源视频没有可校验的版本")
        return {"etag": etag, "size_bytes": int(result.content_length)}
    except (HTTPException, ValueError):
        raise
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            raise ValueError("MARKETPLACE_SOURCE_NOT_READY:源视频不存在") from exc
        raise unavailable() from exc


def download_version(key: str, path: Path, etag: str) -> None:
    """按冻结的 ETag 下载；上游覆盖同名视频时明确终止旧快照。"""
    try:
        bucket().get_object_to_file(key, str(path), headers={"If-Match": etag})
    except HTTPException:
        raise
    except Exception as exc:
        if getattr(exc, "status", None) == 412:
            raise ValueError("MARKETPLACE_SOURCE_CHANGED:源视频已更新，请重新选择") from exc
        if getattr(exc, "status", None) == 404:
            raise ValueError("MARKETPLACE_SOURCE_NOT_READY:源视频不存在") from exc
        raise unavailable() from exc


@transport
def signed(key: str, content_type: str = "application/octet-stream") -> str:
    """使用上传时的对象 Content-Type；不覆盖响应头，避免 OSS 拒绝预览。"""
    return bucket().sign_url("GET", key, get_marketplace_settings().signed_url_seconds,
                             slash_safe=True)
