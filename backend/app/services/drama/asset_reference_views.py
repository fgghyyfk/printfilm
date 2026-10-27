"""Read-only asset responses with reference identities scoped to their project."""

from __future__ import annotations

from copy import deepcopy

from fastapi import HTTPException

from app.models import User
from app.schemas_drama import DramaAssetOut
from app.schemas_media_references import MediaReferenceOut
from app.services.drama.access import get_owned_drama_project
from app.services.tasks.generation_references import enrich_references, public_references


async def clean_asset_reference_params(db, params, *, user_id: int, team_id: int | None,
                                       project_id: int) -> dict | None:
    """Copy current/version snapshots, retaining URLs while authorizing each identity."""
    if not isinstance(params, dict):
        return None
    cleaned = deepcopy(params)

    async def clean_snapshot(snapshot):
        if isinstance(snapshot, dict) and "references" in snapshot:
            snapshot["references"] = await enrich_references(
                db, snapshot["references"], user_id=user_id, team_id=team_id, project_id=project_id,
            )

    await clean_snapshot(cleaned.get("media_metadata"))
    for field in ("image_versions", "video_versions"):
        versions = cleaned.get(field)
        if not isinstance(versions, list):
            continue
        for version in versions:
            if isinstance(version, dict):
                await clean_snapshot(version)
                await clean_snapshot(version.get("media_metadata"))
    return cleaned


async def asset_reference_view(db, asset, *, user_id: int, project=None) -> DramaAssetOut:
    """Reuse caller authorization, or authorize the project before building a copy."""
    if project is None:
        project = await get_owned_drama_project(db, asset.project_id, User(id=user_id))
    if (project.id != asset.project_id
            or (project.team_id is None and project.user_id != user_id)):
        raise HTTPException(status_code=404, detail="资产不存在")
    params = await clean_asset_reference_params(
        db, asset.params, user_id=user_id, team_id=project.team_id, project_id=project.id,
    )
    output = DramaAssetOut.model_validate(asset)
    output.params = params
    metadata = params.get("media_metadata") if isinstance(params, dict) else None
    references = metadata.get("references") if isinstance(metadata, dict) else []
    output.references = [MediaReferenceOut(**reference) for reference in public_references(references)]
    return output
