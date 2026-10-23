"""唯一的原项目接入点：能力发现、路由挂载和独立表初始化。"""
from fastapi import Depends, FastAPI

from .config import get_marketplace_settings


def install(app: FastAPI) -> None:
    """默认不加载模型、路由、后台进程；能力发现可用于隐藏菜单。"""
    @app.get("/api/marketplace/capabilities", tags=["下游能力"], response_model=dict)
    async def capabilities():
        """仅报告配置状态，不将配置完整误报为 OSS 联调通过。"""
        from .storage import configured
        enabled = get_marketplace_settings().enabled
        from app.config import get_settings
        settings = get_settings()
        return {"enabled": enabled, "storage_configured": enabled and configured(), "payments": enabled and bool(settings.epay_pid and settings.epay_key and settings.epay_api_url)}

    if not get_marketplace_settings().enabled:
        return
    from . import admin, api, authorization, media, uploads, workspace, series, commerce, handoff
    from .dependencies import enabled
    from .models import MarketBase
    from app.database import engine

    for router in (api.router, uploads.router, workspace.router, media.router, authorization.router, series.router, commerce.router, handoff.router):
        app.include_router(router, prefix="/api/marketplace", dependencies=[Depends(enabled)], tags=["下游作品与招募"])
    app.include_router(admin.router, prefix="/api/admin/marketplace", dependencies=[Depends(enabled)], tags=["下游运营"])

    @app.on_event("startup")
    async def initialize_marketplace():
        """只创建独立 metadata 中的新表，不迁移原业务数据。"""
        async with engine.begin() as conn:
            await conn.run_sync(MarketBase.metadata.create_all)
        from .runtime import start_marketplace_runtime
        await start_marketplace_runtime()

    @app.on_event("shutdown")
    async def shutdown_marketplace():
        from .runtime import stop_marketplace_runtime
        await stop_marketplace_runtime()
