# Admin work audit (visibility / audit_status)
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database import get_db
from app.deps import get_current_admin
from app.models import User, Work
from app.schemas import AdminWorkListOut, AdminWorkOut, AdminWorkPatch, PageMeta

router = APIRouter()


@router.get("/works", response_model=AdminWorkListOut,
    summary="分页查询已发布作品",
    description="管理员按审核状态、可见性、标题关键词和所属用户查询已发布作品，按作品编号倒序返回。",
    response_description="操作成功，返回结果详见响应结构",
)
async def list_works(
    audit_status: str | None = None,
    visibility: str | None = None,
    q: str | None = None,
    user_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminWorkListOut:
    # Paginated published works
    owner = aliased(User)
    stmt = select(Work, owner.email).outerjoin(owner, owner.id == Work.user_id)
    count_stmt = select(func.count()).select_from(Work)

    if audit_status and audit_status.strip():
        stmt = stmt.where(Work.audit_status == audit_status.strip())
        count_stmt = count_stmt.where(Work.audit_status == audit_status.strip())
    if visibility and visibility.strip():
        stmt = stmt.where(Work.visibility == visibility.strip())
        count_stmt = count_stmt.where(Work.visibility == visibility.strip())
    if user_id is not None:
        stmt = stmt.where(Work.user_id == user_id)
        count_stmt = count_stmt.where(Work.user_id == user_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        filt = Work.title.ilike(like)
        stmt = stmt.where(filt)
        count_stmt = count_stmt.where(filt)

    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    rows = (
        await db.execute(
            stmt.order_by(Work.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()

    items: list[AdminWorkOut] = []
    for work, email in rows:
        data = AdminWorkOut.model_validate(work)
        data.user_email = email
        items.append(data)

    return AdminWorkListOut(items=items, meta=PageMeta(page=page, page_size=page_size, total=total))


@router.patch("/works/{work_id}", response_model=AdminWorkOut,
    summary="更新作品审核状态与可见性",
    description="管理员局部更新已发布作品。visibility 支持 public（公开）、private（私有）、unlisted（不列出）；audit_status 支持 pending（待审核）、passed（通过）、rejected（拒绝）。",
    response_description="操作成功，返回结果详见响应结构",
)
async def patch_work(
    work_id: int,
    body: AdminWorkPatch,
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminWorkOut:
    # Update visibility / audit_status
    work = await db.get(Work, work_id)
    if not work:
        raise HTTPException(status_code=404, detail="作品不存在")

    if body.visibility is not None:
        vis = body.visibility.strip()
        if vis not in ("public", "private", "unlisted"):
            raise HTTPException(status_code=400, detail="visibility 无效")
        work.visibility = vis

    if body.audit_status is not None:
        audit = body.audit_status.strip()
        if audit not in ("pending", "passed", "rejected"):
            raise HTTPException(status_code=400, detail="audit_status 无效")
        work.audit_status = audit

    await db.commit()
    await db.refresh(work)

    owner = await db.get(User, work.user_id)
    data = AdminWorkOut.model_validate(work)
    data.user_email = owner.email if owner else None
    return data
