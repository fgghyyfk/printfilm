"""分集梗概入口、生成失败和旧分镜标识的离线回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.drama.episodes import _episode_out
from app.services.drama import agents
from app.services.drama.seed import _script_body_fingerprint


async def test_full_episode_uses_existing_synopsis_without_regenerating_it(monkeypatch):
    """用户已确认梗概时，不强制补创意或让模型重写梗概。"""
    synopsis = "小松鼠藏好松果，冬天找到部分存粮吃饱，春天发现遗忘的种子发芽。" * 3
    body = "### 场1-1\n日 外 松树林\n出场人物：朵朵\n△ 朵朵埋好种子。\n" * 20
    chat = AsyncMock(return_value={"episodes": [{"episodeNumber": 1, "title": "春芽", "content": body}]})
    monkeypatch.setattr(agents, "drama_chat_json", chat)
    rows = await agents.run_episode_full_from_creative(
        {"episodeCount": 1}, [], 1, "", summary=synopsis, title="春芽",
    )
    assert rows[0]["summary"] == synopsis
    assert rows[0]["creative"] == ""
    assert rows[0]["body"] == body.strip()
    assert chat.await_count == 1
    assert synopsis in chat.call_args.args[1]


@pytest.mark.parametrize("mode", ["body", "batch", "optimize"])
async def test_generated_body_still_too_short_after_retry_fails(monkeypatch, mode):
    """不能把连续失败的短稿作为成功结果传到自动分镜。"""
    chat = AsyncMock(return_value={"episodes": [{"episodeNumber": 1, "content": "只有一句梗概。"}]})
    monkeypatch.setattr(agents, "drama_chat_json", chat)
    with pytest.raises(ValueError, match="正文仍过短"):
        if mode == "body":
            await agents.run_episode_body_from_brief({}, [], 1, creative="", summary="本集故事梗概。" * 12)
        elif mode == "batch":
            await agents.run_episode_script_batch({"episodeCount": 1}, [], total=1)
        else:
            await agents.run_episode_script_from_draft({}, [], 1, "用户的故事草稿。" * 10)
    assert chat.await_count == 2


@pytest.mark.parametrize("notes", ["", "保留温暖结尾，不添加反派"])
async def test_extract_synopsis_preserves_optional_creation_notes(monkeypatch, notes):
    """从正文提取梗概时，不反推或覆盖用户的可选创作备注。"""
    synopsis = "小松鼠认真储粮过冬，春天发现遗忘的种子发芽，和朋友一起照顾树苗。" * 2
    monkeypatch.setattr(agents, "drama_chat_json", AsyncMock(return_value={
        "episodes": [{"episodeNumber": 1, "summary": synopsis, "creative": "模型擅自补写的创意"}],
    }))
    body = "△ 松鼠埋好松果，冬天找到存粮，春天看见树苗。\n" * 8
    existing = [{"episodeNumber": 1, "creative": notes}]
    rows = await agents.run_episode_brief_from_body({}, existing, 1, body)
    assert rows[0]["creative"] == notes
    assert rows[0]["summary"] == synopsis
    assert rows[0]["body"] == body.strip()


@pytest.mark.parametrize("changed", [False, True])
def test_episode_output_marks_stale_storyboard_without_mutating_params(changed):
    """正文变更时只标注旧镜，保留镜头并且不写入 ORM params。"""
    body = "### 场1-1\n日 外 森林\n△ 松鼠埋果。"
    params = {"episodeNumber": 1, "fragment_source_fp": _script_body_fingerprint(body)}
    frag = SimpleNamespace(
        id=2, episode_id=1, sort_order=0, content="@duration:5\n空镜：森林。",
        cover="", video="", duration_sec=5, params={}, asset_references=[],
    )
    episode = SimpleNamespace(id=1, name="春芽", project_id=1, params=params, fragments=[frag])
    current_body = body + "\n△ 松鼠抬头。" if changed else body
    out = _episode_out(episode, script_bodies={1: current_body})
    assert out.params["fragment_script_changed"] is changed
    assert "fragment_script_changed" not in params
    assert len(out.fragments) == 1
