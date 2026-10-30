"""事务提交后立即启动文字任务，不提交的任务绝不调用上游。"""
from sqlalchemy import event

_PENDING = "direct_llm_after_commit"
_REGISTERED = "direct_llm_hooks_registered"


def launch_direct_task(task_id: int, lease_token: str) -> None:
    """复用运行协程的租约、取消和停机处理，不经过调度器领取。"""
    from app.services.tasks.scheduler import start_direct_task
    start_direct_task(task_id, lease_token)


def defer_until_commit(db, task_id: int, lease_token: str) -> None:
    """支持调用方 commit=False 的业务原子提交，回滚清除待启动任务。"""
    session = db.sync_session
    transaction = session.get_nested_transaction() or session.get_transaction()
    session.info.setdefault(_PENDING, {}).setdefault(transaction, []).append((task_id, lease_token))
    if session.info.get(_REGISTERED):
        return
    session.info[_REGISTERED] = True

    def after_commit(committed_session):
        """仅在提交成功后启动，任务使用独立数据库会话。"""
        current = committed_session.get_nested_transaction() or committed_session.get_transaction()
        pending = committed_session.info.get(_PENDING, {})
        entries = pending.pop(current, [])
        if current is not None and current.nested:
            pending.setdefault(current.parent, []).extend(entries)
            return
        committed_session.info.pop(_PENDING, None)
        for ident, token in entries:
            launch_direct_task(ident, token)

    def after_rollback(rolled_back_session):
        """业务或额度验证回滚时不产生后台模型调用。"""
        current = rolled_back_session.get_nested_transaction() or rolled_back_session.get_transaction()
        if current is not None and current.nested:
            rolled_back_session.info.get(_PENDING, {}).pop(current, None)
        else:
            rolled_back_session.info.pop(_PENDING, None)

    event.listen(session, "after_commit", after_commit)
    event.listen(session, "after_rollback", after_rollback)
