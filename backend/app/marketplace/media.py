"""按业务上下文签名；不根据调用者提供的 OSS key 发放链接。"""
import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import select

from . import storage
from .config import get_marketplace_settings
from .dependencies import DB, Visitor, storage_ready
from .models import Asset, EpisodePreview, Publication, Recruit, Rights, Series, SeriesEpisode, Submission, Support, Work
from .service import event, fail, is_public_work, recruit_open, rights_for, rights_valid, submission_rights_match

router = APIRouter()


@router.get("/media/{kind}/{record_id}/{purpose}", response_model=dict, dependencies=[Depends(storage_ready)])
async def media_url(kind: str, record_id: int, purpose: str, db: DB, user: Visitor):
    """每次签名前鉴权；签名最长5分钟，既有链接不能即时撤回。"""
    asset, key, content_type = None, None, "video/mp4"
    admin = bool(user and user.role == "admin")
    if kind == "work":
        work = await db.get(Work, record_id)
        if not work or purpose not in ("preview", "cover"):
            fail("媒体不存在", 404)
        owner = bool(user and work.owner_id == user.id)
        if not admin and (work.blocked or (not owner and not await is_public_work(db, record_id))):
            fail("作品未公开或已下架", 404)
        asset = await db.get(Asset, work.asset_id) if work.asset_id else None
        if purpose == "preview" and asset:
            # 广场播放只签发水印文件。作者和管理员查看原片走 asset 接口。
            from .previews import is_watermark_key, preview_for
            preview = await preview_for(db, work)
            if not preview or preview.status != "done" or not is_watermark_key(preview.object_key):
                fail("水印播放文件尚未生成，暂不可播放", 409)
            key, content_type = preview.object_key, "video/mp4"
        if purpose == "cover" and work.cover_asset_id:
            asset = await db.get(Asset, work.cover_asset_id)
    elif kind == "series_episode":
        episode = await db.get(SeriesEpisode, record_id)
        series = await db.get(Series, episode.series_id) if episode else None
        if not episode or not series or purpose not in ("preview", "cover"):
            fail("媒体不存在", 404)
        owner = bool(user and episode.owner_id == user.id)
        public = bool(series.listed and series.audit_status == "approved" and episode.listed and episode.audit_status == "approved" and not series.blocked and not episode.blocked)
        if not admin and not owner and not public:
            fail("媒体未公开或已下架", 404)
        if purpose == "cover":
            asset = await db.get(Asset, episode.cover_asset_id or series.cover_asset_id) if (episode.cover_asset_id or series.cover_asset_id) else None
        else:
            asset = await db.get(Asset, episode.asset_id) if episode.asset_id else None
            # Paid episodes never fall back to the original object.  A legacy
            # row may still have preview_status="none"; that must be treated as
            # not-ready and return 409 below rather than leaking the source.
            from .previews import is_watermark_key
            preview = (await db.execute(select(EpisodePreview).where(
                EpisodePreview.episode_id == episode.id, EpisodePreview.asset_id == episode.asset_id,
                EpisodePreview.status == "done").order_by(EpisodePreview.id.desc()).limit(1))).scalar_one_or_none()
            if not preview or not is_watermark_key(preview.object_key):
                fail("水印播放文件尚未生成，暂不可播放", 409)
            key, content_type = preview.object_key, "video/mp4"
    elif kind == "series":
        series = await db.get(Series, record_id)
        if not series or purpose != "cover" or not series.cover_asset_id:
            fail("媒体不存在", 404)
        owner = bool(user and series.owner_id == user.id)
        public = bool(series.listed and series.audit_status == "approved" and not series.blocked)
        if not admin and not owner and not public:
            fail("媒体未公开或已下架", 404)
        asset = await db.get(Asset, series.cover_asset_id)
    elif kind == "submission":
        item = await db.get(Submission, record_id)
        post = await db.get(Recruit, item.recruit_id) if item else None
        if not item or not user or (not admin and user.id not in (item.owner_id, post.owner_id)) or purpose not in ("review", "cover"):
            fail("投稿不存在或无权访问", 404)
        rights = await rights_for(db, item.asset_id)
        if not admin and (item.withdrawn or item.blocked or post.blocked or not rights_valid(rights) or not submission_rights_match(item, rights)):
            fail("投稿未完成基础核验、材料已变更或已被撤回、封禁", 403)
        asset = await db.get(Asset, item.asset_id)
    elif kind == "asset":
        asset = await db.get(Asset, record_id)
        if not asset or not user or (not admin and asset.owner_id != user.id):
            fail("文件不存在或无权访问", 404)
        if purpose not in ("original", "preview", "review", "cover"):
            fail("媒体用途无效")
        if purpose == "original" or (purpose == "preview" and asset.kind == "video"):
            image = asset.kind != "video" and bool(asset.cover_key)
            key = asset.cover_key if image else asset.object_key
            content_type = "image/png" if image else asset.content_type
        event(db, user.id, "asset", asset.id, "signed_" + purpose)
    elif kind == "recruit":
        post = await db.get(Recruit, record_id)
        if not post or not purpose.startswith("image-"):
            fail("媒体不存在", 404)
        private = bool(user and (admin or post.owner_id == user.id))
        if not private and not recruit_open(post):
            # 已投稿的人仍可看需求参考资料。
            related = user and (await db.execute(select(Submission.id).where(Submission.recruit_id == post.id, Submission.owner_id == user.id).limit(1))).scalar_one_or_none()
            if not related or post.blocked:
                fail("需求已关闭或不可见", 404)
        try:
            index = int(purpose.split("-", 1)[1])
            if index < 0:
                raise ValueError
            asset = await db.get(Asset, post.image_ids[index])
        except (ValueError, IndexError):
            fail("参考图片不存在", 404)
        key, content_type = asset.cover_key or asset.object_key, "image/png" if asset.cover_key else asset.content_type
    elif kind == "support":
        support = await db.get(Support, 1)
        if not user or purpose != "qr" or not support or not support.enabled or not support.qr_asset_id:
            fail("客服暂不可用或尚未登录", 404)
        asset = await db.get(Asset, support.qr_asset_id)
        key, content_type = asset.cover_key or asset.object_key, "image/png" if asset.cover_key else asset.content_type
    else:
        fail("媒体类型无效", 404)
    if not asset or asset.status != "ready":
        fail("媒体尚未就绪", 409)
    if purpose == "cover" and asset.kind != "video":
        key = asset.cover_key or asset.object_key
        content_type = "image/png" if asset.cover_key else asset.content_type
    if key is None:
        key = getattr(asset, f"{purpose}_key", None)
        if purpose == "cover":
            content_type = "image/jpeg" if asset.kind == "video" else "image/png"
    if not key:
        fail("该预览尚未生成", 409)
    url = await asyncio.to_thread(storage.signed, key, content_type)
    await db.commit()
    return {"url": url, "expires_in": get_marketplace_settings().signed_url_seconds}
