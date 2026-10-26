import httpx
import pytest
from sqlalchemy import func, select

from app.database import get_db
from app.deps import get_current_admin, get_current_user
from app.main import app
from app.models import UsageEvent, User
from app.models_tasks import TaskRun
from app.services.media_models.prices import active_book, publish_book
from conftest import make_user


@pytest.fixture
async def client(db_session):
    user = await make_user(db_session)
    user_id = user.id
    await db_session.commit()
    async def database():
        yield db_session
    async def current_user():
        return await db_session.get(User, user_id, populate_existing=True)
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.update({get_db: database, get_current_user: current_user,
                                    get_current_admin: current_user})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
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
async def test_stale_catalog_and_unpriced_resolution_never_create_task(client, db_session):
    http, user = client
    book = await publish_book(db_session, prices={"seedream-5.0-pro": {"2K": "0.10"}}, expected_version=None, user_id=user.id)
    await db_session.commit()
    before = await db_session.scalar(select(func.count(TaskRun.id)))
    response = await http.post("/api/tools/run", headers={"X-Media-Price-Version": str(book["version"] + 1)}, data={
        "tool_id": "t2i", "prompt": "test", "model_id": "seedream-5.0-pro"})
    assert response.status_code == 409, response.text
    response = await http.post("/api/tools/run", data={"tool_id": "t2i",
        "prompt": "test", "model_id": "seedream-5.0-pro", "resolution": "1K"})
    assert response.status_code == 422, response.text
    assert await db_session.scalar(select(func.count(TaskRun.id))) == before


@pytest.mark.asyncio
async def test_partial_inline_delivery_is_persisted_and_charged_once(client, db_session):
    from app.services.billing.ephemeral import run_billed_ephemeral
    from app.services.billing.context import get_current_task_run_id
    from app.services.billing.usage import record_line

    _, user = client
    await publish_book(db_session, prices={"seedream-5.0-pro": {"2K": "0.1234"}}, expected_version=None, user_id=user.id)
    async def deliver():
        task = await db_session.get(TaskRun, get_current_task_run_id())
        task.result_payload = {"kind": "image", "urls": ["/static/one.png", "/static/two.png"]}
        for index in range(2):
            await record_line(db_session, user_id=user.id, billing_key="seedream", model="seedream-5.0-pro",
                domain="studio", raw={"delivery_id": f"stored-image-{index}"})
        raise RuntimeError("provider unavailable")
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await run_billed_ephemeral(db_session, user, domain="studio", task_type="tool_image", executor=deliver,
            payload={"model": "seedream-5.0-pro", "count": 3}, commit=True)
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
    task = TaskRun(domain="studio", task_type="tool_video", requested_by=user.id, payload={"model": "seedance-2.5", "resolution": "720p", "duration": 5})
    await prepare_task_media(db_session, task)
    response = await http.patch("/api/admin/settings/billing/media-prices/activation", json={"enabled": False, "expected_version": book["version"]})
    assert response.status_code == 200
    assert await active_book(db_session) is None
    assert task.billing_snapshot["mode"] == "fixed_media"
    response = await http.post("/api/tools/run", headers={"X-Media-Price-Version": str(book["version"])}, data={
        "tool_id": "t2v", "prompt": "test", "model_id": "seedance-2.5", "duration": "5",
        "resolution": "720p", "video_mode": "text_to_video"})
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_normal_user_cannot_publish_prices(client, db_session):
    http, user = client
    app.dependency_overrides.pop(get_current_admin)
    response = await http.post("/api/admin/settings/billing/media-prices", json={"prices": {}, "expected_version": None})
    assert response.status_code == 403
    assert await active_book(db_session) is None
