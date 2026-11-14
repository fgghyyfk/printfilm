"""公开错误只返回固定诊断，不泄漏上游密钥、地址或本机路径。"""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from app.api import tools
from app.schemas import ProjectOut
from app.schemas_tasks import TaskRunOut


RAW = "服务异常 sk-synthetic-secret https://upstream.invalid/private?key=synthetic /private/tmp/synthetic.trace"


async def _run_t2i(db, user):
    """按真实表单值直调工具路由（合并后生图改走 studio_generation 任务平台入队）。"""
    return await tools.run_tool(
        tool_id="t2i", prompt="一只猫", negative="", ratio="", strength="", mode="", pack="", duration="",
        motion="", voice="narrator_calm", model_id="", resolution="", video_mode="", references="[]",
        price_version=None, model_asset_id=None, color_ref=None, files=[], db=db, user=user,
    )


def assert_private_absent(value):
    """用合成敏感片段核对整个公开响应。"""
    text = str(value)
    for token in ("sk-synthetic", "upstream.invalid", "/private/tmp", "synthetic.trace"):
        assert token not in text


@pytest.mark.parametrize("error,status", [
    (ValueError("请填写提示词"), 400),
    (RuntimeError(RAW), 500),
    (httpx.HTTPStatusError(RAW, request=httpx.Request("POST", "https://upstream.invalid"),
                           response=httpx.Response(401)), 502),
    (httpx.HTTPStatusError(RAW, request=httpx.Request("POST", "https://upstream.invalid"),
                           response=httpx.Response(403)), 502),
    (RuntimeError("Credits insufficient " + RAW), 502),
])
async def test_tools_errors_have_safe_message_code_and_correct_status(monkeypatch, error, status):
    """真实工具路由保留参数/上游/服务失败的区别，不能将其误报成本地钱包402。"""
    monkeypatch.setattr(tools, "enqueue_generation", AsyncMock(side_effect=error))
    with pytest.raises(HTTPException) as info:
        await _run_t2i(AsyncMock(), SimpleNamespace(id=1))
    assert info.value.status_code == status
    assert_private_absent(info.value.detail)
    assert info.value.headers.get("X-Error-Code")
    assert "余额不足" not in info.value.detail


async def test_tools_preserve_local_authentication_http_exception(monkeypatch):
    """本站明确认证异常仍为401，不能被外层except吞掉变500。"""
    monkeypatch.setattr(tools, "enqueue_generation", AsyncMock(side_effect=HTTPException(401, "登录已失效")))
    with pytest.raises(HTTPException) as info:
        await _run_t2i(AsyncMock(), SimpleNamespace(id=1))
    assert info.value.status_code == 401
    assert info.value.detail == "登录已失效"


async def test_real_wallet_failure_remains_402(db_session, monkeypatch):
    """由真实钱包校验产生的不足才进入402，且不改变估价或金额。"""
    from app.config import get_settings
    from app.models_tasks import TaskRun
    from app.services.billing import settlement
    from tests.conftest import make_user

    user = await make_user(db_session, balance_fen=0)
    monkeypatch.setattr(get_settings(), "billing_enabled", True)
    monkeypatch.setattr(settlement, "estimate_task_fen", AsyncMock(return_value=100))
    monkeypatch.setattr(settlement, "pending_task_commitment_fen", AsyncMock(return_value=0))

    async def enqueue(*args, **kwargs):
        """调用现有实际钱包探测而非伪造余额异常。"""
        await settlement.ensure_balance_for_task(db_session, user, TaskRun(domain="studio", task_type="tool_image"))

    monkeypatch.setattr(tools, "enqueue_generation", enqueue)
    with pytest.raises(HTTPException) as info:
        await _run_t2i(db_session, user)
    assert info.value.status_code == 402
    assert "余额不足" in info.value.detail
    assert info.value.headers["X-Error-Code"] == "insufficient_balance"


def test_project_history_output_is_safe_without_changing_source():
    """历史成片详情读出时脱敏，不回写原始数据库/审计内容。"""
    source = dict(id=1, template_id="test", title="作品", source_type="theme", source_text=RAW,
                  status="FAILED", progress=0, error_msg=RAW, cover_url=None, final_video_url=None,
                  resolution_mode="preview", ref_image_url=None, created_at=datetime.now(UTC),
                  updated_at=datetime.now(UTC))
    public = ProjectOut.model_validate(source).model_dump()
    assert_private_absent(public["error_msg"])
    assert public["source_text"] == RAW
    assert source["error_msg"] == RAW


async def test_task_history_only_maps_known_diagnostic_fields(monkeypatch):
    """任务、步骤和事件中的诊断定向处理，业务prompt/result文字不做递归字符串替换。"""
    source = dict(id=1, domain="drama", task_type="fragment_video", status="failed", priority=100,
                  requested_by=1, error_message=RAW, payload={"prompt": RAW},
                  result_payload={"error": RAW, "text": RAW},
                  steps=[dict(id=2, step_key="main", step_type="job", status="failed", error_message=RAW)],
                  events=[dict(id=3, event_type="task.failed", message=RAW, payload={"prompt": RAW})])
    from app.api import tasks

    row = TaskRunOut.model_validate(source)
    monkeypatch.setattr(tasks, "get_task_for_user", AsyncMock(return_value=row))
    public = (await tasks.get_task_run(1, db=AsyncMock(), user=SimpleNamespace(id=1))).model_dump()
    for value in (public["error_message"], public["steps"][0]["error_message"],
                  public["events"][0]["message"], public["result_payload"]["error"]):
        assert_private_absent(value)
    assert public["payload"]["prompt"] == public["result_payload"]["text"] == RAW
    assert public["events"][0]["payload"]["prompt"] == RAW
    assert source["error_message"] == source["result_payload"]["error"] == RAW
    # 管理诊断共享的原始模型、原日志均不被公开投影改写。
    assert row.model_dump()["events"][0]["message"] == RAW


def test_drama_and_tool_history_map_diagnostics_without_mutating_params():
    """资产/分镜/分集及工具套图的旧诊断共享公开映射，但文案和原JSON保持。"""
    from app.schemas_drama import DramaAssetOut, DramaEpisodeOut, DramaFragmentOut
    from app.schemas_ecom import EcomSetItemOut
    from app.schemas_tools import ToolRunRecordOut

    params = {"generation": {"status": "failed", "error": RAW, "root_error": RAW},
              "fragment_plan_error": RAW, "visualPrompt": RAW}
    models = [
        DramaAssetOut(id=1, type="image", asset_type="character", project_id=1, params=params),
        DramaFragmentOut(id=1, episode_id=1, sort_order=0, content=RAW, params=params),
        DramaEpisodeOut(id=1, revision=0, name="分集", project_id=1, params=params),
    ]
    for model in models:
        output = model.model_dump()
        assert_private_absent(output["params"]["generation"]["error"])
        assert_private_absent(output["params"]["generation"]["root_error"])
        assert_private_absent(output["params"]["fragment_plan_error"])
        assert output["params"]["visualPrompt"] == RAW
    item = EcomSetItemOut(idx=0, slot="main_white", slot_label="白底", layout="none", ratio="1:1",
                          scene="", title="", subtitle="", bullets=[], status="failed", error=RAW)
    tool = ToolRunRecordOut(id=1, tool_id="t2i", kind="image", status="failed", error=RAW, created_at="")
    assert_private_absent(item.model_dump()["error"])
    assert_private_absent(tool.model_dump()["error"])
    assert params["generation"]["error"] == params["fragment_plan_error"] == RAW


async def test_sse_replayed_failure_is_safe_copy_but_normal_progress_is_unchanged(monkeypatch):
    """真实SSE生成器处理旧缓存失败副本，普通进度不泛化清洗。"""
    import asyncio
    import json
    from app.api import projects

    queue = asyncio.Queue()
    progress = {"event": "progress", "message": RAW, "task_id": 7}
    failure = {"event": "failed", "message": RAW, "task_id": 7}
    queue.put_nowait(progress)
    queue.put_nowait(failure)
    monkeypatch.setattr(projects, "_get_owned_project", AsyncMock())
    monkeypatch.setattr(projects, "subscribe", lambda _id: queue)
    monkeypatch.setattr(projects, "unsubscribe", lambda *_args: None)
    monkeypatch.setattr(projects, "redis_bridge", AsyncMock())
    response = await projects.project_events(1, db=AsyncMock(), user=SimpleNamespace(id=1))
    iterator = response.body_iterator
    assert json.loads((await anext(iterator))["data"]) == progress
    public = json.loads((await anext(iterator))["data"])
    assert_private_absent(public["message"])
    assert public["task_id"] == 7 and public["code"]
    assert failure["message"] == RAW
    await iterator.aclose()
