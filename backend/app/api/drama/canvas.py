"""Drama canvas persistence (nodes stored as project content + none-type assets)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.models_drama import DramaAsset
from app.schemas_drama import DramaCanvasSaveRequest
from app.services.drama.access import get_owned_drama_project

router = APIRouter()


def _team_nodes(nodes: list, assets: list[DramaAsset]) -> list:
    """团队布局中的资产快照始终由当前资产生成，旧画布不能回写资产。"""
    by_id = {asset.id: asset for asset in assets}
    out = []
    for raw in nodes:
        node = dict(raw)
        data = dict(node.get("data") or {})
        try:
            asset = by_id.get(int(data.get("assetId") or 0))
        except (TypeError, ValueError):
            asset = None
        if asset:
            data.update(label=asset.name, name=asset.name, kind=asset.type,
                        asset_type=asset.asset_type, mediaUrl=asset.url,
                        url=asset.url, cover=asset.cover)
        node["data"] = data
        out.append(node)
    return out


@router.get("/canvas/{project_id}")
async def get_canvas(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    project = await get_owned_drama_project(db, project_id, user, with_assets=True)
    content = project.content if isinstance(project.content, dict) else {}
    return {
        "project_id": project_id,
        "nodes": _team_nodes(content.get("canvas_nodes") or [], project.assets or []) if project.team_id else content.get("canvas_nodes") or [],
        "edges": content.get("canvas_edges") or [],
    }


@router.post("/canvas")
async def save_canvas(
    body: DramaCanvasSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    project = await get_owned_drama_project(db, body.project_id, user, with_assets=True)
    content = dict(project.content) if isinstance(project.content, dict) else {}
    content["canvas_nodes"] = body.nodes
    content["canvas_edges"] = body.edges
    project.content = content

    if project.team_id is not None:
        # 团队资产通过独立资产接口和 asset 租约修改；画布租约仅控制布局。
        content["canvas_nodes"] = _team_nodes(body.nodes, project.assets or [])
        project.content = content
        await db.commit()
        return {"ok": True, "nodes": len(body.nodes), "edges": len(body.edges)}

    # Upsert canvas nodes as type=none assets keyed by node id in params
    existing = {
        str((a.params or {}).get("canvas_node_id")): a
        for a in (project.assets or [])
        if a.type == "none" and (a.params or {}).get("canvas_node_id")
    }
    seen: set[str] = set()
    for node in body.nodes:
        nid = str(node.get("id") or "")
        if not nid:
            continue
        seen.add(nid)
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        name = str(data.get("label") or data.get("name") or f"节点 {nid}")
        url = data.get("mediaUrl") or data.get("url") or data.get("cover")
        kind = str(data.get("kind") or "none")
        asset_type = str(
            data.get("asset_type")
            or ("video" if kind == "video" else "audio" if kind == "audio" else "image")
        )
        # Prefer matching by explicit assetId, then by canvas_node_id
        asset_id_raw = data.get("assetId")
        matched = None
        if asset_id_raw is not None:
            try:
                aid = int(asset_id_raw)
            except (TypeError, ValueError):
                aid = None
            if aid is not None:
                matched = next((a for a in (project.assets or []) if a.id == aid), None)
        if matched is None:
            matched = existing.get(nid)

        if matched is not None:
            asset = matched
            asset.name = name
            if kind and kind != "none":
                asset.type = kind
            asset.asset_type = asset_type
            if url:
                asset.url = str(url)
                # 音频/音色节点勿把媒体 URL 写进 cover（否则 Seedance 会当参考图）
                if asset_type in {"audio", "voice"} or kind in {"audio", "voice"}:
                    asset.cover = None
                else:
                    asset.cover = str(url)
            params = dict(asset.params or {})
            params["canvas"] = node
            params["canvas_node_id"] = nid
            asset.params = params
            existing[nid] = asset
        else:
            is_audio_node = asset_type in {"audio", "voice"} or kind in {"audio", "voice"}
            db.add(
                DramaAsset(
                    project_id=project.id,
                    type=kind if kind else "none",
                    asset_type=asset_type,
                    name=name,
                    cover=None if is_audio_node else (str(url) if url else None),
                    url=str(url) if url else None,
                    params={"canvas_node_id": nid, "canvas": node},
                )
            )
    await db.commit()
    return {"ok": True, "nodes": len(body.nodes), "edges": len(body.edges)}
