"""公开目录和作者工作台；只操作 marketplace_ 表。"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, literal, select, union_all

from app.models import User
from .dependencies import Actor, DB, Visitor
from .previews import prepare_preview, preview_ready, requires_preview
from .models import Asset, Handoff, PlaybackJob, Publication, Recruit, RegistrationCase, RevenueConfig, Rights, Series, SeriesEpisode, Submission, Work
from .schemas import CaseInput, RecruitInput, RecruitUpdate, RightsInput, Version, WorkInput, WorkUpdate
from .service import (asset_owned, asset_owned_for_draft, check_version, data, ensure_kept, event, fail, get_target, is_public_work, now,
                      notify, owned, page, public_recruit_filters, public_work_filters, recruit_data,
                      recruit_open, rights_for, rights_valid, submission_data, validate_rights, work_data)

router = APIRouter()


async def _pause_work_sales(db, work_id: int) -> None:
    publications = list((await db.execute(select(Publication).where(
        Publication.content_type == "work", Publication.content_id == work_id,
    ).with_for_update())).scalars().all())
    for publication in publications:
        publication.listed = False


async def _ensure_work_idle(db, work_id: int) -> None:
    active = (await db.execute(select(Handoff.id).where(
        Handoff.target_kind == "work", Handoff.target_id == work_id,
        Handoff.status.in_(("pending", "running")),
    ).limit(1))).scalar_one_or_none()
    if active:
        fail("MARKETPLACE_SYNC_IN_PROGRESS", 409)


@router.get("/categories", response_model=dict)
async def get_categories(db: DB):
    from .categories import category_config
    return await category_config(db)


async def catalog_item(db, kind: str, item_id: int) -> dict:
    """把单视频和短剧收成广场卡片；短剧不展开分集正文。"""
    if kind == "work":
        payload = await work_data(db, await db.get(Work, item_id))
        payload["kind"] = "work"
        return payload
    from .series import _series_data
    series = await db.get(Series, item_id)
    detail = await _series_data(db, series)
    owner = await db.get(User, series.owner_id)
    return {"kind": "series", "id": series.id, "title": series.title, "description": series.description,
            "category": series.category, "sale_type": series.sale_type, "price_fen": series.copyright_price_fen,
            "author": owner.nickname if owner else "创作者", "episode_count": detail["episode_count"],
            "free_episode_count": series.free_episode_count,
            "cover_asset_id": series.cover_asset_id, "created_at": series.created_at}


@router.get("/works", response_model=dict)
async def works(db: DB, q: str = Query("", max_length=100), category: str = Query("", max_length=60),
                min_price: int | None = Query(None, ge=0), max_price: int | None = Query(None, ge=0),
                sort: str = "latest", page_number: int = Query(1, alias="page", ge=1), page_size: int = Query(20, ge=1, le=100)):
    """单视频和短剧共用作品广场，筛选后再分页。"""
    from .series import public_series_filters
    if sort not in {"latest", "price_asc", "price_desc"}:
        fail("排序参数无效")
    work_query = select(Work.id.label("item_id"), literal("work").label("kind"), Work.created_at.label("sort_at"),
                        Work.price_fen.label("price")).join(Asset, Asset.id == Work.asset_id).join(Rights, Rights.asset_id == Asset.id).where(*public_work_filters())
    series_query = select(Series.id.label("item_id"), literal("series").label("kind"), Series.created_at.label("sort_at"),
                          Series.copyright_price_fen.label("price")).where(*public_series_filters())
    if q:
        work_query = work_query.where(Work.title.ilike(f"%{q}%"))
        series_query = series_query.where(Series.title.ilike(f"%{q}%"))
    if category:
        work_query = work_query.where(Work.category == category)
        series_query = series_query.where(Series.category == category)
    if min_price is not None:
        work_query = work_query.where(Work.price_fen >= min_price)
        series_query = series_query.where(Series.copyright_price_fen >= min_price)
    if max_price is not None:
        work_query = work_query.where(Work.price_fen <= max_price)
        series_query = series_query.where(Series.copyright_price_fen <= max_price)
    combined = union_all(work_query, series_query).subquery()
    total = (await db.execute(select(func.count()).select_from(combined))).scalar_one()
    ordering = {"latest": combined.c.sort_at.desc(), "price_asc": combined.c.price.asc(), "price_desc": combined.c.price.desc()}
    rows = (await db.execute(select(combined).order_by(ordering[sort], combined.c.item_id.desc())
                             .offset((page_number - 1) * page_size).limit(page_size))).all()
    return {"items": [await catalog_item(db, row.kind, row.item_id) for row in rows],
            "meta": {"page": page_number, "page_size": page_size, "total": total}}


@router.get("/works/{work_id}", response_model=dict)
async def work_detail(work_id: int, db: DB, user: Visitor):
    """作者和管理员可看草稿，公众仅可看满足全部条件的作品。"""
    item = await db.get(Work, work_id)
    private = bool(item and user and not item.deleted_at and (item.owner_id == user.id or user.role == "admin"))
    if not item or item.deleted_at or (not private and not await is_public_work(db, work_id)):
        fail("作品不存在或未公开", 404)
    return await work_data(db, item, private)


@router.post("/works", response_model=dict)
async def create_work(body: WorkInput, db: DB, user: Actor):
    """文字草稿无需 OSS，绑定媒体时只能选择本人的 ready 资产。"""
    from .categories import validate_category
    await validate_category(db, body.category)
    if body.asset_id:
        await asset_owned_for_draft(db, body.asset_id, user)
    if body.cover_asset_id:
        await asset_owned(db, body.cover_asset_id, user, ("image",))
    values = body.model_dump()
    # 兼容旧客户端未发送收费类型且 price_fen=0 的草稿；新客户端明确选择联系客服时保留该类型。
    if "sale_type" not in body.model_fields_set and values["price_fen"] == 0:
        values["sale_type"] = "free"
    item = Work(owner_id=user.id, **values)
    db.add(item)
    await db.commit()
    return await work_data(db, item, True)


@router.put("/works/{work_id}", response_model=dict)
async def update_work(work_id: int, body: WorkUpdate, db: DB, user: Actor):
    """更新即撤下旧公开内容；权利范围变化重置完整核验。"""
    item = await owned(db, Work, work_id, user)
    ensure_kept(item)
    check_version(item, body.version)
    await _ensure_work_idle(db, item.id)
    from .categories import validate_category
    await validate_category(db, body.category, item.category)
    if body.asset_id:
        await asset_owned_for_draft(db, body.asset_id, user)
    if body.cover_asset_id:
        await asset_owned(db, body.cover_asset_id, user, ("image",))
    if item.license_scope != body.license_scope or item.source != body.source:
        rights = await rights_for(db, body.asset_id, lock=True)
        if rights:
            rights.full_status = "draft"
            rights.version += 1
    values = body.model_dump(exclude={"version"})
    if "sale_type" not in body.model_fields_set and values["price_fen"] == 0:
        values["sale_type"] = "free"
    for key, value in values.items():
        setattr(item, key, value)
    item.version += 1
    item.audit_status, item.listed, item.reason = "draft", False, ""
    await _pause_work_sales(db, item.id)
    event(db, user.id, "work", item.id, "updated", version=item.version)
    await db.commit()
    return await work_data(db, item, True)


@router.post("/works/{work_id}/submit", response_model=dict)
async def submit_work(work_id: int, body: Version, db: DB, user: Actor):
    """作品资料和版权信息一次提交，进入统一管理员审核。"""
    item = await owned(db, Work, work_id, user)
    ensure_kept(item)
    check_version(item, body.version)
    await _ensure_work_idle(db, item.id)
    if item.blocked:
        fail("作品已被管理员封禁")
    if item.audit_status in ("pending", "processing", "approved"):
        return await work_data(db, item, True)
    await asset_owned(db, item.asset_id or 0, user)
    rights = await rights_for(db, item.asset_id, lock=True)
    if not item.license_scope.strip():
        fail("请在作品草稿中填写“拟授权用途及限制”后再提交审核")
    if not rights or rights.blocked or rights.details.get("source") != item.source:
        fail("请先填写版权信息和授权意向")
    validate_rights(rights.details, full=True, certificate=bool(rights.certificate_asset_id))
    if rights.valid_until and rights.valid_until <= now():
        fail("版权授权已过期")
    # 免费作品直接播放用户上传的原片，不创建试看/水印任务；收费作品
    # 才需要由市场 Worker 生成最多 15 秒、最多原片 30% 的试看文件。
    watermark_ready = await preview_ready(db, item) if requires_preview(item.sale_type) else True
    if requires_preview(item.sale_type) and not watermark_ready:
        from .dependencies import storage_ready
        await storage_ready()
        await prepare_preview(db, item)
        item.audit_status, item.listed, item.reason = "processing", False, "正在生成试看播放文件"
    else:
        item.audit_status, item.listed, item.reason = "pending", False, ""
    config = None
    if item.sale_type == "platform_unlock":
        config = await db.get(RevenueConfig, 1)
        if not config or not config.enabled:
            fail("MARKETPLACE_REVENUE_CONFIG_REQUIRED", 409)
        if item.unlock_price_fen <= 0:
            fail("平台投放作品必须设置解锁价", 409)
    if item.audit_status in ("pending", "processing"):
        await _pause_work_sales(db, item.id)
        publication = (await db.execute(select(Publication).where(
            Publication.content_type == "work", Publication.content_id == item.id,
            Publication.version == item.version).with_for_update())).scalar_one_or_none()
        if publication and publication.audit_status == "approved":
            item.version += 1
            publication = None
        values = dict(owner_id=user.id, version=item.version, asset_id=item.asset_id,
                      cover_asset_id=item.cover_asset_id, sale_type=item.sale_type,
                      copyright_price_fen=item.price_fen, unlock_price_fen=item.unlock_price_fen,
                      share_bps=item.share_bps if item.share_bps is not None else (config.platform_share_bps if config else None), rights_snapshot={"details": rights.details,
                      "certificate_asset_id": rights.certificate_asset_id, "evidence_ids": rights.evidence_ids},
                      audit_status=item.audit_status if item.audit_status == "pending" else "draft",
                      preview_status=("done" if watermark_ready else "pending") if requires_preview(item.sale_type) else "none",
                      playback_status="done" if item.sale_type != "platform_unlock" else "pending")
        if publication is None:
            publication = Publication(content_type="work", content_id=item.id, **values)
            db.add(publication)
        else:
            for key, value in values.items():
                setattr(publication, key, value)
        # A durable playback job gives the market Worker a lease and restart
        # recovery point.  It is intentionally created only for platform
        # content; free/contact works never need HLS packaging.
        if item.sale_type == "platform_unlock":
            playback_job = (await db.execute(select(PlaybackJob).where(
                PlaybackJob.publication_id == publication.id
            ).with_for_update())).scalar_one_or_none() if publication.id else None
            if playback_job is None:
                # New publications have no database id until flush; the FK is
                # populated after the explicit flush below.
                await db.flush()
                playback_job = (await db.execute(select(PlaybackJob).where(
                    PlaybackJob.publication_id == publication.id
                ).with_for_update())).scalar_one_or_none()
            if playback_job is None:
                db.add(PlaybackJob(publication_id=publication.id, status="pending"))
            else:
                # Re-submitting the same draft version invalidates any prior
                # package attempt; never leave a done job paired with a pending
                # publication snapshot.
                playback_job.status, playback_job.error = "pending", ""
                playback_job.attempts = 0
                playback_job.lease_token, playback_job.lease_until = None, None
            publication.playback_manifest_key = None
            publication.playback_error = ""
    rights.basic_status = rights.full_status = "pending"
    if item.audit_status == "pending":
        event(db, user.id, "work", item.id, "submitted", version=item.version)
    await db.commit()
    return await work_data(db, item, True)


@router.post("/works/{work_id}/listing", response_model=dict)
async def listing(work_id: int, body: Version, db: DB, user: Actor, listed: bool = True):
    """作者主动上下架，不允许自行解除管理员封禁。"""
    item = await owned(db, Work, work_id, user)
    ensure_kept(item)
    check_version(item, body.version)
    if listed:
        if requires_preview(item.sale_type) and not await preview_ready(db, item):
            fail("水印播放文件尚未生成，请重新提交审核", 409)
        rights = await rights_for(db, item.asset_id, lock=True)
        asset = await db.get(Asset, item.asset_id) if item.asset_id else None
        if item.blocked or item.audit_status != "approved" or not asset or asset.status != "ready" or not rights_valid(rights, full=True) or rights.details.get("source") != item.source:
            fail("媒体、内容或权属尚未通过，不能上架")
        if item.sale_type == "platform_unlock":
            publication = (await db.execute(select(Publication).where(Publication.content_type == "work", Publication.content_id == item.id, Publication.version == item.version).with_for_update())).scalar_one_or_none()
            if not publication or publication.playback_status != "done":
                fail("平台播放版本尚未就绪，请稍后再上架", 409)
    item.listed = listed
    await _pause_work_sales(db, item.id)
    publication = (await db.execute(select(Publication).where(Publication.content_type == "work", Publication.content_id == item.id, Publication.version == item.version).with_for_update())).scalar_one_or_none()
    if publication:
        publication.listed = listed and publication.audit_status == "approved" and (item.sale_type != "platform_unlock" or publication.playback_status == "done")
    event(db, user.id, "work", item.id, "listed" if listed else "unlisted", version=item.version)
    await db.commit()
    return await work_data(db, item, True)


@router.delete("/works/{work_id}", response_model=dict)
async def delete_work(work_id: int, db: DB, user: Actor):
    """作者软删单视频。广场和我的作品不再展示，原片与审核记录保留。"""
    item = await owned(db, Work, work_id, user)
    from .service import retire_owned
    await retire_owned(db, item, user, "work")
    await db.commit()
    return {"ok": True}


@router.get("/assets/{asset_id}/rights", response_model=dict)
async def get_rights(asset_id: int, db: DB, user: Actor):
    """权属材料只允许本人或管理员读取，并保留访问记录。"""
    await owned(db, Asset, asset_id, user, admin=True)
    rights = await rights_for(db, asset_id)
    if not rights:
        source = (await db.execute(select(Work.source).where(Work.asset_id == asset_id, Work.owner_id == user.id).order_by(Work.id.desc()).limit(1))).scalar_one_or_none()
        return {"asset_id": asset_id, "version": 0, "details": {"source": source or "external"}}
    event(db, user.id, "rights", rights.id, "read")
    await db.commit()
    return data(rights, "id", "asset_id", "version", "details", "certificate_asset_id", "evidence_ids", "basic_status", "full_status", "valid_until", "blocked", "reason")


@router.put("/assets/{asset_id}/rights", response_model=dict)
async def save_rights(asset_id: int, body: RightsInput, db: DB, user: Actor):
    """任何材料变动都重置核验结论，绑定不可变视频文件。"""
    await asset_owned(db, asset_id, user)
    for document_id in [*body.evidence_ids, *([body.certificate_asset_id] if body.certificate_asset_id else [])]:
        await asset_owned(db, document_id, user, ("document", "image"))
    if body.valid_until and body.valid_until <= now():
        fail("授权截止时间必须晚于当前时间")
    authorization = None
    if body.details.source == "printfilm":
        from .authorization import current
        authorization = await current(db, user.id)
        if not authorization:
            fail("请先签署 PrintFilm 授权函")
    item = await rights_for(db, asset_id, lock=True)
    if item:
        check_version(item, body.version)
        item.version += 1
    else:
        if body.version != 0:
            fail("权属档案版本无效", 409)
        item = Rights(asset_id=asset_id, owner_id=user.id, version=1)
        db.add(item)
    item.details = body.details.model_dump()
    if body.details.source == "printfilm":
        from .authorization import current
        authorization = await current(db, user.id)
        if authorization:
            item.details = {**item.details, "authorization_id": str(authorization.id),
                            "authorization_version": authorization.template_version,
                            "authorization_signer": authorization.signer_name}
        if not authorization:
            fail("请先签署 PrintFilm 授权函")
    item.certificate_asset_id, item.evidence_ids, item.valid_until = body.certificate_asset_id, body.evidence_ids, body.valid_until
    item.basic_status, item.full_status = "draft", "draft"
    related = (await db.execute(select(Work).where(Work.asset_id == asset_id).with_for_update())).scalars().all()
    for work in related:
        await _ensure_work_idle(db, work.id)
        work.audit_status, work.listed = "draft", False
        work.version += 1
        await _pause_work_sales(db, work.id)
    related_episodes = list((await db.execute(select(SeriesEpisode).where(
        SeriesEpisode.asset_id == asset_id).with_for_update())).scalars().all())
    if related_episodes:
        from .series import ensure_series_idle, pause_series_sales
        for episode in related_episodes:
            episode.version += 1
            episode.audit_status, episode.listed = "draft", False
        for series_id in sorted({episode.series_id for episode in related_episodes}):
            series = (await db.execute(select(Series).where(Series.id == series_id).with_for_update())).scalar_one()
            await ensure_series_idle(db, series.id)
            series.version += 1
            series.audit_status, series.listed = "draft", False
            await pause_series_sales(db, series)
    await db.flush()
    event(db, user.id, "rights", item.id, "updated", version=item.version)
    await db.commit()
    return {"id": item.id, "version": item.version}


@router.post("/rights/{rights_id}/submit", response_model=dict)
async def submit_rights(rights_id: int, body: Version, db: DB, user: Actor, full: bool = False):
    """用户只提交一次版权审核；保留 full 参数兼容旧客户端。"""
    item = await owned(db, Rights, rights_id, user)
    if (await db.execute(select(Work.id).where(Work.asset_id == item.asset_id).limit(1))).scalar_one_or_none():
        fail("请提交作品审核，版权信息将一并提交", 409)
    check_version(item, body.version)
    if item.blocked:
        fail("权属档案处于争议或封禁中")
    validate_rights(item.details, full=full, certificate=bool(item.certificate_asset_id))
    if not item.evidence_ids and item.details.get("source") not in ("printfilm", "external"):
        fail("请上传创作或授权证明材料")
    if item.details.get("source") == "external" and not item.evidence_ids and not item.certificate_asset_id:
        fail("请上传基础权属证明；没有证明可先联系客服协办")
    item.basic_status = item.full_status = "pending"
    event(db, user.id, "rights", item.id, "submitted", version=item.version)
    await db.commit()
    return {"ok": True}


@router.put("/rights/{rights_id}/case", response_model=dict)
async def save_case(rights_id: int, body: CaseInput, db: DB, user: Actor):
    """申请回执和案件办结从不修改权属核验结论。"""
    await owned(db, Rights, rights_id, user)
    item = (await db.execute(select(RegistrationCase).where(RegistrationCase.rights_id == rights_id).with_for_update())).scalar_one_or_none()
    if item:
        check_version(item, body.version)
        item.version += 1
        item.status = "pending_materials"
    else:
        if body.version != 0:
            fail("案件版本无效", 409)
        item = RegistrationCase(rights_id=rights_id, owner_id=user.id)
        db.add(item)
    item.route, item.official_reference, item.note = body.route, body.official_reference, body.note
    await db.commit()
    return data(item, "id", "rights_id", "version", "route", "status", "official_reference", "note")


@router.post("/assets/{asset_id}/assistance", response_model=dict)
async def start_assistance(asset_id: int, db: DB, user: Actor):
    """无证明即可建协办案；不生成虚假权属信息或审核结论。"""
    await asset_owned(db, asset_id, user)
    rights = await rights_for(db, asset_id, lock=True)
    if rights and rights.details.get("source") == "printfilm":
        fail("PrintFilm 作品请先签署授权函；更正来源请保存权属档案")
    if not rights:
        rights = Rights(asset_id=asset_id, owner_id=user.id, details={"source": "external"})
        db.add(rights)
        await db.flush()
    item = (await db.execute(select(RegistrationCase).where(RegistrationCase.rights_id == rights.id).with_for_update())).scalar_one_or_none()
    if item and item.route != "assisted":
        fail("已有其他办理方式，请在案件表单中改为客服协办", 409)
    if not item:
        item = RegistrationCase(rights_id=rights.id, owner_id=user.id, route="assisted")
        db.add(item)
        await db.flush()
        event(db, user.id, "case", item.id, "created_assistance")
        notify(db, user.id, f"权属协办案件 #{item.id} 已建立，请联系客服补充材料")
    await db.commit()
    return data(item, "id", "rights_id", "version", "route", "status", "official_reference", "note")


@router.get("/rights/{rights_id}/case", response_model=dict)
async def get_case(rights_id: int, db: DB, user: Actor):
    """按当前档案读取案件，避免个人案件分页截断导致重复创建。"""
    await owned(db, Rights, rights_id, user)
    item = (await db.execute(select(RegistrationCase).where(RegistrationCase.rights_id == rights_id))).scalar_one_or_none()
    return {"item": data(item, "id", "rights_id", "version", "route", "status", "official_reference", "note") if item else None}


@router.get("/recruits", response_model=dict)
async def recruits(db: DB, q: str = Query("", max_length=100), category: str = Query("", max_length=60),
                   min_budget: int | None = Query(None, ge=0), max_budget: int | None = Query(None, ge=0),
                   sort: str = "latest", page_number: int = Query(1, alias="page", ge=1), page_size: int = Query(20, ge=1, le=100)):
    """只展示审核通过且仍开放的需求。"""
    query = select(Recruit).where(*public_recruit_filters())
    if q:
        query = query.where(Recruit.title.ilike(f"%{q}%"))
    if category:
        query = query.where(Recruit.category == category)
    if min_budget is not None:
        query = query.where(Recruit.budget_fen >= min_budget)
    if max_budget is not None:
        query = query.where(Recruit.budget_fen <= max_budget)
    if sort not in ("latest", "deadline"):
        fail("排序参数无效")
    records, meta = await page(db, query.order_by(Recruit.deadline_at if sort == "deadline" else Recruit.id.desc(), Recruit.id.desc()), page_number, page_size)
    return {"items": [await recruit_data(db, item) for item in records], "meta": meta}


@router.get("/recruits/{post_id}", response_model=dict)
async def recruit_detail(post_id: int, db: DB, user: Visitor):
    """相关投稿人可看已关闭需求，但不能借此看他人的投稿。"""
    item = await db.get(Recruit, post_id)
    private = bool(item and user and (user.id == item.owner_id or user.role == "admin"))
    related = bool(user and (await db.execute(select(Submission.id).where(Submission.recruit_id == post_id, Submission.owner_id == user.id).limit(1))).scalar_one_or_none())
    if not item or (not private and not related and not recruit_open(item)):
        fail("需求不存在或未公开", 404)
    result = await recruit_data(db, item, private)
    result.update(is_owner=bool(user and user.id == item.owner_id), is_open=recruit_open(item),
                  can_submit=False,
                  can_contact=bool(user and user.id != item.owner_id and recruit_open(item)))
    return result


@router.post("/recruits", response_model=dict)
async def create_recruit(body: RecruitInput, db: DB, user: Actor):
    """没有参考图的文字需求不依赖 OSS。"""
    if body.deadline_at <= now():
        fail("截止时间必须晚于当前时间")
    for image in body.image_ids:
        await asset_owned(db, image, user, ("image",))
    item = Recruit(owner_id=user.id, **body.model_dump())
    db.add(item)
    await db.commit()
    return await recruit_data(db, item, True)


@router.put("/recruits/{post_id}", response_model=dict)
async def update_recruit(post_id: int, body: RecruitUpdate, db: DB, user: Actor):
    """曾有投稿后锁定实质条款；其他修改撤下并重新审核。"""
    item = await owned(db, Recruit, post_id, user)
    check_version(item, body.version)
    if item.state in ("closed", "expired"):
        fail("需求已结束，请重新发布")
    if body.deadline_at <= now():
        fail("截止时间必须晚于当前时间")
    exists = (await db.execute(select(Submission.id).where(Submission.recruit_id == post_id).limit(1))).scalar_one_or_none()
    locked = ("budget_fen", "wanted_count", "license_scope", "requirements", "deadline_at", "description")
    if exists and any(getattr(item, field) != getattr(body, field) for field in locked):
        fail("已有投稿，预算、用途、授权、交付及截止条件已锁定，请关闭后重新发布")
    for image in body.image_ids:
        await asset_owned(db, image, user, ("image",))
    for key, value in body.model_dump(exclude={"version"}).items():
        setattr(item, key, value)
    item.version += 1
    item.audit_status, item.state = "draft", "draft"
    await db.commit()
    return await recruit_data(db, item, True)


@router.post("/recruits/{post_id}/submit", response_model=dict)
async def submit_recruit(post_id: int, body: Version, db: DB, user: Actor):
    """需求审核独立于原项目发布流程。"""
    item = await owned(db, Recruit, post_id, user)
    check_version(item, body.version)
    if item.blocked or item.state in ("closed", "expired") or item.deadline_at <= now():
        fail("需求已结束或被封禁")
    item.audit_status = "pending"
    event(db, user.id, "recruit", item.id, "submitted", version=item.version)
    await db.commit()
    return {"ok": True}


@router.post("/recruits/{post_id}/close", response_model=dict)
async def close_recruit(post_id: int, db: DB, user: Actor):
    """关闭后退出广场并停止创作者的新接单咨询，历史记录保留。"""
    item = await owned(db, Recruit, post_id, user)
    item.state = "closed"
    item.version += 1
    event(db, user.id, "recruit", item.id, "closed", version=item.version)
    await db.commit()
    return {"ok": True}


@router.post("/recruits/{post_id}/submissions", response_model=dict, include_in_schema=False)
async def create_submission(post_id: int, user: Actor):
    """兼容旧客户端并明确拒绝投稿；不创建媒体任务或修改资产。"""
    raise HTTPException(410, detail={"code": "MARKETPLACE_SUBMISSIONS_DISABLED",
                                     "message": "招募已改为客服撮合，请在招募详情中联系客服接单"})


@router.get("/recruits/{post_id}/submissions", response_model=dict)
async def received_submissions(post_id: int, db: DB, user: Actor, page_number: int = Query(1, alias="page", ge=1), page_size: int = Query(20, ge=1, le=100)):
    """收到的投稿只有需求方和管理员可列出。"""
    await owned(db, Recruit, post_id, user, admin=True)
    records, meta = await page(db, select(Submission).where(Submission.recruit_id == post_id).order_by(Submission.id.desc()), page_number, page_size)
    return {"items": [await submission_data(db, item, user) for item in records], "meta": meta}


@router.post("/submissions/{submission_id}/withdraw", response_model=dict)
async def withdraw(submission_id: int, db: DB, user: Actor):
    """撤回即停止新签名，历史留痕不删除。"""
    item = await owned(db, Submission, submission_id, user)
    item.withdrawn = True
    event(db, user.id, "submission", item.id, "withdrawn")
    await db.commit()
    return {"ok": True}


@router.post("/submissions/{submission_id}/selection", response_model=dict, include_in_schema=False)
async def select_submission(submission_id: int, user: Actor):
    """停止线上选稿；历史标记只读保留。"""
    raise HTTPException(410, detail={"code": "MARKETPLACE_SUBMISSIONS_DISABLED",
                                     "message": "招募已改为客服撮合，请联系客服确认合作"})
