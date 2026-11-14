# -*- coding: utf-8 -*-
"""注册限流、媒体路径不可猜、已受理任务取消计费、提权邮箱保护。"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.auth import register, update_me
from app.config import get_settings
from app.schemas import ProfileUpdateRequest, RegisterRequest
from app.services import register_limit, storage
from app.services.tasks import executor
from app.services.tasks.service import _mark_task_cancelled_stale
from app.services.teams.content_media import MediaCopier
from tests.conftest import make_task, make_user
from tests.tts_database import db_session  # noqa: F401


def _request(forwarded: str = "", client: str = "10.0.0.1") -> Request:
    """构造带转发头的请求。"""
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": (client, 1)})


@pytest.fixture(autouse=True)
def _isolated_limits(monkeypatch):
    """限流走进程内计数，用例互不影响。"""
    register_limit._memory.clear()
    monkeypatch.setattr("app.services.password_reset.get_redis_client", lambda: None)


# ---- 注册 ----
def test_client_ip_ignores_spoofed_left_entries():
    assert register_limit.client_ip(_request("1.2.3.4, 8.8.8.8, 172.18.0.2")) == "8.8.8.8"
    assert register_limit.client_ip(_request("", "172.18.0.2")) == "172.18.0.2"


def test_register_limit_per_hour():
    results = [register_limit.register_allowed("8.8.8.8") for _ in range(register_limit.HOUR_LIMIT + 1)]
    assert all(results[:-1]) and results[-1] is False
    assert register_limit.register_allowed("8.8.4.4") is True


def test_grant_limit_per_day():
    results = [register_limit.grant_allowed("8.8.8.8") for _ in range(register_limit.GRANT_DAY_LIMIT + 1)]
    assert results == [True] * register_limit.GRANT_DAY_LIMIT + [False]


def test_signup_grant_defaults_off():
    assert type(get_settings()).model_fields["billing_signup_grant_fen"].default == 0


async def test_register_grant_stops_after_ip_limit(db_session, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "billing_signup_grant_fen", 500)
    grants = []
    from app.services.auth import get_user_by_email

    for i in range(register_limit.GRANT_DAY_LIMIT + 1):
        email = f"grant{i}-{id(grants)}@example.com"
        await register(RegisterRequest(email=email, password="QaTest12345!", nickname="QA"),
                       request=_request("8.8.8.8"), db=db_session)
        grants.append(int((await get_user_by_email(db_session, email)).balance_fen))
    assert grants == [500] * register_limit.GRANT_DAY_LIMIT + [0]


async def test_register_rejected_when_over_hour_limit(monkeypatch):
    monkeypatch.setattr(register_limit, "register_allowed", lambda _ip: False)
    with pytest.raises(HTTPException) as exc:
        await register(RegisterRequest(email="a@example.com", password="QaTest12345!", nickname="QA"),
                       request=_request("8.8.8.8"), db=None)
    assert exc.value.status_code == 429


# ---- 媒体路径 ----
def test_new_project_dir_is_unguessable(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "GENERATED_ROOT", tmp_path)
    path = storage.project_dir(7)
    assert path.name.startswith("p7_") and len(path.name) == len("p7_") + 16
    assert storage.project_dir(7) == path
    assert storage.project_dir(70) != path


def test_legacy_project_keeps_its_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "GENERATED_ROOT", tmp_path)
    (tmp_path / "p8").mkdir()
    assert storage.project_dir(8) == tmp_path / "p8"
    assert not list(tmp_path.glob("p8_*"))


def test_dir_survives_secret_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "GENERATED_ROOT", tmp_path)
    path = storage.project_dir(9)
    monkeypatch.setattr(get_settings(), "secret_key", "rotated-secret")
    assert storage.project_dir(9) == path


def test_ecom_public_dir_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "GENERATED_ROOT", tmp_path)
    assert storage.project_dir(0).name == "p0"


async def test_delete_removes_every_project_dir(tmp_path, monkeypatch):
    from app.services import pipeline

    monkeypatch.setattr(storage, "GENERATED_ROOT", tmp_path)
    (tmp_path / "p5").mkdir()
    (tmp_path / "p5_abc").mkdir()
    (tmp_path / "p50_abc").mkdir()
    await pipeline.delete_project_assets(5)
    assert [p.name for p in tmp_path.iterdir()] == ["p50_abc"]


async def test_team_media_copy_accepts_new_dir_name(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    monkeypatch.setattr(storage, "GENERATED_ROOT", tmp_path / "generated")
    source = storage.project_dir(3) / "a.png"
    source.write_bytes(b"x")
    copier = MediaCopier(4, {3})
    url = await copier.rewrite(f"/static/generated/{source.parent.name}/a.png")
    assert url != f"/static/generated/{source.parent.name}/a.png"
    assert (storage.project_dir(4)).exists()


# ---- 已受理任务取消计费 ----
async def _frozen(db, provider):
    """创建预扣 100 分的任务。"""
    user = await make_user(db, balance_fen=900, frozen_fen=100)
    task = await make_task(db, user, domain="drama", task_type="fragment_video", status="cancel_requested",
                           billing_status="frozen", provider_task_id=provider)
    task.billing_estimate_fen = 100
    await db.flush()
    return user, task


async def test_cancel_after_upstream_accept_is_charged(db_session, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "billing_enabled", True)
    user, task = await _frozen(db_session, "up-1")
    loaded = await executor.get_task_for_runtime(db_session, task.id)
    await executor._mark_cancelled(db_session, loaded)
    await db_session.refresh(user)
    assert int(user.balance_fen) == 900 and int(user.frozen_fen) == 0  # 预扣转实扣，不退


async def test_cancel_before_upstream_accept_is_refunded(db_session, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "billing_enabled", True)
    user, task = await _frozen(db_session, None)
    loaded = await executor.get_task_for_runtime(db_session, task.id)
    await executor._mark_cancelled(db_session, loaded)
    await db_session.refresh(user)
    assert int(user.balance_fen) == 1000 and int(user.frozen_fen) == 0  # 全额退回


async def test_stale_cancel_after_accept_is_charged(db_session, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "billing_enabled", True)
    user, task = await _frozen(db_session, "up-2")
    await _mark_task_cancelled_stale(db_session, task, "分镜已变更", datetime.now(UTC))
    await db_session.commit()
    await db_session.refresh(user)
    assert int(user.balance_fen) == 900 and int(user.frozen_fen) == 0  # 预扣转实扣，不退


# ---- 提权邮箱 ----
async def test_cannot_change_email_to_bootstrap_address(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_bootstrap_emails", "Boss@Example.com, x@example.com")
    user = SimpleNamespace(id=1, email="me@example.com", nickname="n", phone=None)
    with pytest.raises(HTTPException) as exc:
        await update_me(ProfileUpdateRequest(nickname="n", email="boss@example.com"), db=None, user=user)
    assert exc.value.status_code == 400
