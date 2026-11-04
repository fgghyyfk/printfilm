"""分镜规划入队前的锁外固定估价。"""
from app.models_tasks import TaskRun
from app.services.billing.estimates import estimate_task_fen
from app.services.billing.settlement import billing_active


async def prepare_fragment_plan_estimate(db) -> int:
    """规划估价不依赖分集内容，冷价目先在分集/钱包锁外准备。"""
    if not billing_active():
        return 0
    return await estimate_task_fen(db, TaskRun(domain="drama", task_type="fragment_plan"))
