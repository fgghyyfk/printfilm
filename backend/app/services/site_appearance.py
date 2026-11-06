"""站点外观：全站主题由管理员在后台切换，存在 app_settings 的独立一行，不随模型配置保存被覆盖。"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models_settings import AppSettings

APPEARANCE_ROW_ID = "appearance"
SITE_THEMES = ("daylight", "studio")
DEFAULT_SITE_THEME = "daylight"


async def get_site_theme(db: AsyncSession) -> str:
    """读取当前主题；未设置或已下线的主题回退默认。"""
    row = await db.get(AppSettings, APPEARANCE_ROW_ID)
    theme = (row.config_json or {}).get("theme") if row else None
    return theme if theme in SITE_THEMES else DEFAULT_SITE_THEME


async def set_site_theme(db: AsyncSession, theme: str) -> str:
    """保存全站主题；不在可选范围内抛 ValueError。"""
    if theme not in SITE_THEMES:
        raise ValueError("不支持的主题")
    row = await db.get(AppSettings, APPEARANCE_ROW_ID)
    if row is None:
        db.add(AppSettings(id=APPEARANCE_ROW_ID, config_json={"theme": theme}))
    else:
        row.config_json = {**(row.config_json or {}), "theme": theme}
    await db.commit()
    return theme
