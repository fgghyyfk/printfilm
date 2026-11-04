"""TTS 专项使用真实提交和独立连接，结束后显式清理本用例数据。"""
import pytest_asyncio
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import User, Project, Template, Shot, PipelineJob, UsageEvent, WalletLedger
from app.models_drama import DramaProject, DramaAsset
from app.models_tasks import TaskRun


@pytest_asyncio.fixture
async def db_session(db_session, monkeypatch):
    """复用父 fixture 初始化的专用测试库，业务与费用各自真正提交。"""
    from app.services import tts_billing
    sessions = async_sessionmaker(db_session.bind.engine, expire_on_commit=False)
    monkeypatch.setattr(tts_billing, "AsyncSessionLocal", sessions)
    users, templates = set(), set()
    async with sessions() as db:
        def remember_rows(session, context):
            """只追踪当前测试创建的用户和模板，避免清理其它用例的数据。"""
            users.update(obj.id for obj in session.new if isinstance(obj, User))
            templates.update(obj.id for obj in session.new if isinstance(obj, Template))
        event.listen(db.sync_session, "after_flush", remember_rows)
        try:
            yield db
        finally:
            await db.rollback()
            async with sessions() as cleanup:
                projects = select(Project.id).where(Project.user_id.in_(users))
                dramas = select(DramaProject.id).where(DramaProject.user_id.in_(users))
                await cleanup.execute(delete(UsageEvent).where(UsageEvent.user_id.in_(users)))
                await cleanup.execute(delete(WalletLedger).where(WalletLedger.user_id.in_(users)))
                await cleanup.execute(delete(TaskRun).where(TaskRun.requested_by.in_(users)))
                await cleanup.execute(delete(DramaAsset).where(DramaAsset.project_id.in_(dramas)))
                await cleanup.execute(delete(DramaProject).where(DramaProject.user_id.in_(users)))
                await cleanup.execute(delete(Shot).where(Shot.project_id.in_(projects)))
                await cleanup.execute(delete(PipelineJob).where(PipelineJob.project_id.in_(projects)))
                await cleanup.execute(delete(Project).where(Project.user_id.in_(users)))
                await cleanup.execute(delete(Template).where(Template.id.in_(templates)))
                await cleanup.execute(delete(User).where(User.id.in_(users)))
                await cleanup.commit()
