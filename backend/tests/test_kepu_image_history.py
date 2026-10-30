"""Recover AI short-video images from durable task evidence without inventing old outputs."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Project, ProjectStatus, Shot, Template, UsageEvent
from app.models_tasks import TaskRun
from tests.test_generation_history import client_for, history, task  # noqa: F401


async def _project(db, user, *, count=1):
    template = Template(id=f"kepu-history-{uuid.uuid4().hex}", name="History", style_prefix="")
    db.add(template)
    await db.flush()
    now = datetime.now(UTC)
    project = Project(
        user_id=user.id, template_id=template.id, title="AI short video", source_text="A story",
        status=ProjectStatus.DONE, final_video_url="https://output.test/final.mp4",
        updated_at=now + timedelta(minutes=5),
    )
    db.add(project)
    await db.flush()
    shots = [Shot(
        project_id=project.id, shot_no=index,
        image_url=f"https://output.test/project-{project.id}-shot-{index}.png",
        image_ark_url=f"https://provider.test/project-{project.id}-shot-{index}.png",
    ) for index in range(1, count + 1)]
    db.add_all(shots)
    await db.flush()
    return project, shots


async def _image_task(db, user, project, *, phase="assets", shot=None, result=None,
                      status="succeeded", created_at=None):
    at = created_at or datetime.now(UTC)
    row = TaskRun(
        domain="kepu", task_type="shot_regen_image" if shot else "project_pipeline",
        requested_by=user.id, queue_type="image" if phase == "assets" or shot else "video",
        status=status, project_id=project.id, shot_id=shot.id if shot else None,
        payload={"phase": phase} if phase is not None else {}, result_payload=result,
        created_at=at, finished_at=at + timedelta(seconds=1),
    )
    db.add(row)
    await db.flush()
    return row


async def _image_event(db, row, shot, *, raw=None, detail=None, **overrides):
    fields = {
        "user_id": row.requested_by, "team_id": row.team_id,
        "task_run_id": row.id, "project_id": row.project_id, "shot_id": shot.id,
        "domain": "kepu", "capability": "image", "billing_key": "seedream",
        "raw_usage_json": json.dumps({"usage": {"total_tokens": 1}}) if raw is None else raw,
        "billing_detail": detail,
    }
    event = UsageEvent(**{**fields, **overrides})
    db.add(event)
    await db.flush()
    return event


@pytest.mark.parametrize(
    ("phase", "result"),
    [(None, None), ("produce", None), ("produce", {"kind": "video", "urls": []})],
    ids=["old-automatic-stage", "produce-without-snapshot", "produce-with-empty-video-snapshot"],
)
async def test_legacy_image_stage_is_classified_and_restored_after_later_compose(history, phase, result):
    sessions, (user, _, _), _team = history
    async with sessions() as db:
        project, (shot,) = await _project(db, user)
        image = await _image_task(db, user, project, phase=phase, result=result)
        await _image_event(db, image, shot)
        compose = TaskRun(
            domain="kepu", task_type="project_compose_only", queue_type="video", status="succeeded",
            requested_by=user.id, project_id=project.id, payload={"phase": "compose"},
            result_payload={"kind": "video", "urls": [project.final_video_url]},
            created_at=image.created_at + timedelta(minutes=1),
        )
        db.add(compose)
        await db.commit()
    async with await client_for(sessions, user) as client:
        response = await client.get("/api/tasks/generations?kind=image")
        assert response.status_code == 200
        listing = response.json()
        assert listing["total"] == 1
        record = listing["items"][0]
        assert record["platform_task_id"] == image.id
        assert record["kind"] == "image"
        assert record["urls"] == [shot.image_url]
        detail = await client.get(f"/api/tasks/generations/{image.id}")
        assert detail.status_code == 200
        assert detail.json()["kind"] == "image"
        assert detail.json()["urls"] == [shot.image_url]
    async with sessions() as db:
        persisted = await db.get(TaskRun, image.id)
        assert persisted.result_payload == result


@pytest.mark.parametrize("later_is_other_member", [False, True], ids=["outside-page", "other-member"])
async def test_later_redraw_outside_candidate_page_never_becomes_an_old_tasks_output(
    history, later_is_other_member,
):
    sessions, (user, member, _), _team = history
    async with sessions() as db:
        project, (shot,) = await _project(db, user)
        old = await _image_task(db, user, project, created_at=datetime.now(UTC) - timedelta(minutes=2))
        await _image_event(db, old, shot)
        newer = await _image_task(db, member if later_is_other_member else user, project, shot=shot)
        await _image_event(db, newer, shot)
        await db.commit()
    async with await client_for(sessions, user) as client:
        # Date filtering leaves only the old task in the page. The newer usage
        # must still prevent the current image being assigned to that task.
        response = await client.get("/api/tasks/generations", params={
            "kind": "image", "page_size": 1,
            "created_to": (old.created_at + timedelta(seconds=30)).isoformat(),
        })
        assert response.status_code == 200
        listing = response.json()
        assert listing["total"] == 1
        assert listing["items"][0]["platform_task_id"] == old.id
        assert listing["items"][0]["urls"] == []


async def test_manual_upload_is_not_recovered_as_an_old_ai_generation(history):
    sessions, (user, _, _), _team = history
    async with sessions() as db:
        project, (shot,) = await _project(db, user)
        row = await _image_task(db, user, project)
        await _image_event(db, row, shot)
        shot.image_url = "/static/manual-upload.png"
        shot.image_ark_url = None
        await db.commit()
    async with await client_for(sessions, user) as client:
        record = (await client.get(f"/api/tasks/generations/{row.id}")).json()
        assert record["urls"] == []
        assert record["preview_url"] is None


@pytest.mark.parametrize("mismatch", ["user", "team", "project", "shot"])
async def test_mismatched_usage_cannot_supply_an_image_or_private_delivery(history, mismatch):
    sessions, (user, member, _), team = history
    async with sessions() as db:
        project, shots = await _project(db, user, count=2)
        other_project, _ = await _project(db, user)
        row = await _image_task(db, user, project, shot=shots[0])
        overrides = {
            "user": {"user_id": member.id}, "team": {"team_id": team.id},
            "project": {"project_id": other_project.id}, "shot": {"shot_id": shots[1].id},
        }[mismatch]
        await _image_event(db, row, shots[0],
                           raw=json.dumps({"delivery_id": "https://private.test/wrong-output.png"}),
                           **overrides)
        await db.commit()
    async with await client_for(sessions, user) as client:
        response = await client.get(f"/api/tasks/generations/{row.id}")
        assert response.status_code == 200
        assert response.json()["urls"] == []
        assert "private.test" not in response.text


async def test_batch_images_are_preserved_once_even_with_duplicate_usage_rows(history):
    sessions, (user, _, _), _team = history
    async with sessions() as db:
        project, shots = await _project(db, user, count=2)
        row = await _image_task(db, user, project)
        for shot in shots:
            await _image_event(db, row, shot)
        await _image_event(db, row, shots[0])
        await db.commit()
    async with await client_for(sessions, user) as client:
        record = (await client.get(f"/api/tasks/generations/{row.id}")).json()
        assert set(record["urls"]) == {shot.image_url for shot in shots}
        assert len(record["urls"]) == 2
        assert record["preview_url"] in record["urls"]


async def test_explicit_delivery_keeps_the_old_image_after_later_redraw(history):
    sessions, (user, _, _), _team = history
    old_url = "https://output.test/immutable-old-image.png"
    async with sessions() as db:
        project, (shot,) = await _project(db, user)
        old = await _image_task(db, user, project)
        await _image_event(db, old, shot, raw=json.dumps({"delivery_id": old_url}))
        newer = await _image_task(db, user, project, shot=shot)
        await _image_event(db, newer, shot)
        await db.commit()
    async with await client_for(sessions, user) as client:
        record = (await client.get(f"/api/tasks/generations/{old.id}")).json()
        assert record["urls"] == [old_url]
        assert shot.image_url not in record["urls"]


async def test_nonempty_final_video_snapshot_is_not_projected_as_an_image(history):
    """An old automatic image-text pipeline may generate images and compose a video."""
    sessions, (user, _, _), _team = history
    async with sessions() as db:
        project, (shot,) = await _project(db, user)
        project.pipeline_mode = "image_text"
        row = await _image_task(db, user, project, phase="produce", result={
            "kind": "video", "urls": [project.final_video_url],
        })
        await _image_event(db, row, shot)
        await db.commit()
    async with await client_for(sessions, user) as client:
        record = (await client.get(f"/api/tasks/generations/{row.id}")).json()
        assert record["kind"] == "video"
        assert record["urls"] == [project.final_video_url]
        videos = (await client.get("/api/tasks/generations?kind=video")).json()
        assert videos["total"] == 1
        images = (await client.get("/api/tasks/generations?kind=image")).json()
        assert images["total"] == 0


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_unsuccessful_redraw_without_delivery_cannot_borrow_the_previous_image(history, status):
    """A billed attempt may finish without applying its result to the existing shot."""
    sessions, (user, _, _), _team = history
    async with sessions() as db:
        project, (shot,) = await _project(db, user)
        old = await _image_task(db, user, project, result={"kind": "image", "urls": [shot.image_url]})
        await _image_event(db, old, shot)
        redraw = await _image_task(db, user, project, shot=shot, status=status)
        await _image_event(db, redraw, shot, raw=json.dumps({"usage": {"cost_fen": 30}}))
        await db.commit()
    async with await client_for(sessions, user) as client:
        record = (await client.get(f"/api/tasks/generations/{redraw.id}")).json()
        assert record["status"] == status
        assert record["urls"] == []
        previous = (await client.get(f"/api/tasks/generations/{old.id}")).json()
        assert previous["urls"] == [shot.image_url]


async def test_partially_failed_image_batch_keeps_its_successful_delivery(history):
    sessions, (user, _, _), _team = history
    async with sessions() as db:
        project, (shot,) = await _project(db, user)
        row = await _image_task(db, user, project, status="failed")
        row.error_message = "The next image failed"
        await _image_event(db, row, shot, raw=json.dumps({"delivery_id": shot.image_url}),
                           detail={"mode": "fixed_media", "delivered": True})
        await db.commit()
    async with await client_for(sessions, user) as client:
        result = (await client.get("/api/tasks/generations?kind=image&status=failed")).json()
        assert result["total"] == 1
        record = result["items"][0]
        assert record["status"] == "failed"
        assert record["urls"] == [shot.image_url]
        assert record["error"] == "The next image failed"


@pytest.mark.parametrize(
    ("raw", "detail"),
    [("{truncated", None), ("[]", None),
     (json.dumps({"delivery_id": "https://private.test/not-delivered.png"}),
      {"mode": "fixed_media", "delivered": False})],
    ids=["truncated-json", "non-object-json", "no-delivery"],
)
async def test_invalid_or_undelivered_usage_is_ignored_without_breaking_history(history, raw, detail):
    sessions, (user, _, _), _team = history
    async with sessions() as db:
        project, (shot,) = await _project(db, user)
        row = await _image_task(db, user, project)
        await _image_event(db, row, shot, raw=raw, detail=detail)
        await db.commit()
    async with await client_for(sessions, user) as client:
        response = await client.get(f"/api/tasks/generations/{row.id}")
        assert response.status_code == 200
        assert response.json()["urls"] == []
