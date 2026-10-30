"""作品分类由管理员维护；历史作品的分类文字不批量改写。"""
from .models import CategoryConfig
from .service import fail

DEFAULT_CATEGORIES = ["短片", "广告", "动画", "文旅", "科普", "其他"]


async def category_config(db):
    item = await db.get(CategoryConfig, 1)
    return {"version": item.version, "names": item.names} if item else {"version": 0, "names": DEFAULT_CATEGORIES}


async def validate_category(db, name, previous=None):
    if name == previous:
        return
    config = await category_config(db)
    if name not in config["names"]:
        fail("该作品分类未启用，请刷新后选择管理员配置的分类")
