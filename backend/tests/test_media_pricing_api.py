from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.database import get_db
from app.deps import get_current_admin, get_current_user
from app.main import app
from app.models import UsageEvent
from app.models_tasks import TaskRun
from app.services.ark import ImageResult
from app.services.media_models.prices import active_book, publish_book
from conftest import make_user


@pytest.fixture
async def client(db_session):
    user = await make_user(db_session)
    async def database():
        yield db_session
    saved = dict(app.dependency_overrides)
    from app.api.v1.generation import _resolve_api_user
    app.dependency_overrides.update({get_db: database, get_current_user: lambda: user,
                                    get_current_admin: lambda: user, _resolve_api_user: lambda: user})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as http:
        yield http, user
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.mark.asyncio
async def test_admin_publish_catalog_and_quote_have_same_price(client, db_session):
    http, user = client
    response = await http.post("/api/admin/settings/billing/media-prices", json={
        "prices": {"seedream-5.0-pro": {"2K": "0.1234"}}, "expected_version": None,
    })
    assert response.status_code == 200, response.text
    version = response.json()["version"]
    catalog = (await http.get("/api/media-models")).json()
    model = next(item for item in catalog["image_models"] if item["id"] == "seedream-5.0-pro")
    assert model["prices"]["2K"] == "0.1234"
    assert model["available_resolutions"] == ["2K"]
    assert catalog["price_version"] == version
    quote = await http.post("/api/billing/media/quote", json={"price_version": version, "items": [
        {"model_id": "seedream-5.0-pro", "params": {"resolution": "2K", "count": 3}},
    ]})
    assert quote.status_code == 200, quote.text
    assert quote.json()["total_fen"] == 39
    assert quote.json()["price_version"] == version
    assert "api_key" not in quote.text


@pytest.mark.asyncio
async def test_stale_catalog_and_unpriced_resolution_never_create_task(client, db_session, monkeypatch):
    http, user = client
    book = await publish_book(db_session, prices={"seedream-5.0-pro": {"2K": "0.10"}}, expected_version=None, user_id=user.id)
    provider = AsyncMock()
    monkeypatch.setattr("app.api.v1.generation.get_ark", lambda: SimpleNamespace(gen_image=provider))
    before = await db_session.scalar(select(func.count(TaskRun.id)))
    response = await http.post("/api/v1/images/generations", headers={"X-Media-Price-Version": str(book["version"] + 1)}, json={"prompt": "test", "model": "seedream-5.0-pro"})
    assert response.status_code == 409, response.text
    response = await http.post("/api/v1/images/generations", json={"prompt": "test", "model": "seedream-5.0-pro", "resolution": "1K"})
    assert response.status_code == 422, response.text
    assert await db_session.scalar(select(func.count(TaskRun.id))) == before
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_partial_delivery_is_persisted_and_charged_once(client, db_session, monkeypatch):
    http, user = client
    await publish_book(db_session, prices={"seedream-5.0-pro": {"2K": "0.1234"}}, expected_version=None, user_id=user.id)
    provider = AsyncMock(side_effect=[ImageResult(local_url="/static/one.png"),
                                      ImageResult(local_url="/static/two.png"), RuntimeError("provider unavailable")])
    monkeypatch.setattr("app.api.v1.generation.get_ark", lambda: SimpleNamespace(gen_image=provider))
    monkeypatch.setattr("app.services.storage.republish_url", lambda url, **kwargs: url)
    response = await http.post("/api/v1/images/generations", json={"prompt": "test", "model": "seedream-5.0-pro", "count": 3})
    assert response.status_code >= 400
    task = (await db_session.execute(select(TaskRun).where(TaskRun.requested_by == user.id))).scalar_one()
    assert task.status == "failed"
    assert task.billing_estimate_fen == 39
    assert task.billing_charged_fen == 26
    assert task.billing_refunded_fen == 13
    assert task.result_payload["urls"] == ["/static/one.png", "/static/two.png"]
    assert await db_session.scalar(select(func.count(UsageEvent.id)).where(UsageEvent.task_run_id == task.id)) == 2


@pytest.mark.asyncio
async def test_disable_only_affects_new_tasks_and_rejects_stale_fixed_price(client, db_session):
    http, user = client
    book = await publish_book(db_session, prices={"seedance-2.5": {"720p": "0.50"}}, expected_version=None, user_id=user.id)
    from app.services.media_models.planning import prepare_task_media
    task = TaskRun(domain="api", task_type="v1_video", requested_by=user.id, payload={"model": "seedance-2.5", "resolution": "720p", "duration": 5})
    await prepare_task_media(db_session, task)
    response = await http.patch("/api/admin/settings/billing/media-prices/activation", json={"enabled": False, "expected_version": book["version"]})
    assert response.status_code == 200
    assert await active_book(db_session) is None
    assert task.billing_snapshot["mode"] == "fixed_media"
    response = await http.post("/api/v1/videos/generations", headers={"X-Media-Price-Version": str(book["version"])}, json={"prompt": "test", "image_url": "https://test/a.jpg", "model": "seedance-2.5"})
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_normal_user_cannot_publish_prices(client, db_session):
    http, user = client
    app.dependency_overrides.pop(get_current_admin)
    response = await http.post("/api/admin/settings/billing/media-prices", json={"prices": {}, "expected_version": None})
    assert response.status_code == 403
    assert await active_book(db_session) is None
