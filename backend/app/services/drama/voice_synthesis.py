"""漫剧音色资产：用 TokenFree / edge-tts 合成参考音频（供 Seedance reference_audio 使用）。"""
from __future__ import annotations
import logging
import re
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import get_settings
from app.models import User
from app.models_drama import DramaAsset, DramaProject
from app.services.ark import get_ark
from app.services.logical_model_router import resolve_upstream_model
from app.services.billing import record_line
from app.services.drama.voice_reference_audio import finalize_voice_reference_url
from app.services.voices import infer_drama_speaker_from_prompt
logger = logging.getLogger(__name__)
NAME_SUFFIX_PATTERN = re.compile(r"(音色|的声音|语音)$")
# 从 voice 资产名还原角色名（如「禹音色」→「禹」）
def normalize_character_name(name: str | None) -> str:
    raw = (name or "我").strip() or "我"
    return NAME_SUFFIX_PATTERN.sub("", raw).strip() or "我"
# 生成 Seedance 参考音试听句（须 ≥2s，避免 r2v 拒收过短音频）
def build_voice_sample_line_short(character_name: str | None = None) -> str:
    name = normalize_character_name(character_name)
    display = name[:8] if len(name) > 8 else name
    return (
        f"你好，我是{display}。"
        "请听我的语气与声线，之后我会用这样的声音来讲述故事。"
    )
# 生成角色差异化试听台词
def build_voice_sample_text(
    voice_prompt: str,
    character_name: str | None = None,
    *,
    short: bool = False,
) -> str:
    if short:
        return build_voice_sample_line_short(character_name)
    name = normalize_character_name(character_name)
    prompt = (voice_prompt or "").strip()
    blob = f"{name} {prompt}"
    if any(k in blob for k in ("老", "翁", "族老", "首领", "青叔")):
        line = f"{name}：诸位且听我说，此事关乎两岸百姓，不可延误。"
    elif any(k in blob for k in ("禹", "治水", "领袖", "帝王", "君主")):
        line = f"{name}：疏堵并举，通川达海，方能安民。"
    elif any(k in blob for k in ("伯益", "谋士", "儒雅", "书生")):
        line = f"{name}：依我之见，当先察水势，再定工段。"
    elif any(k in blob for k in ("青壮", "少年", "青年", "小伙")):
        line = f"{name}：跟我上，这点工程算不了什么！"
    elif any(k in blob for k in ("百姓", "群众", "平民", "妇人")):
        line = f"{name}：只求河道通畅，我们也好安心过日子。"
    elif any(k in blob for k in ("女", "姑娘", "少女")):
        line = f"{name}：请放心，我会把情况说明白。"
    elif prompt:
        snippet = prompt[:36].rstrip("，。；、 ")
        line = f"{name}：{snippet}。"
    else:
        line = f"{name}：请听我的语气与声线。"
    return line[:180]
async def synthesize_voice_asset(
    db: AsyncSession,
    user: User,
    project: DramaProject,
    asset: DramaAsset,
    *,
    voice_prompt: str,
    sample_text: str | None = None,
    speaker: str | None = None,
    character_name: str | None = None,
    character_asset: DramaAsset | None = None,
) -> DramaAsset:
    """按提示词合成音色参考音频并写回 voice 资产。"""
    settings = get_settings()
    ark = get_ark()
    prompt = (voice_prompt or "").strip()
    if not prompt:
        raise ValueError("缺少音色描述 prompt")
    display_name = normalize_character_name(character_name or asset.name)
    # Seedance reference_audio 须 ≥1.8s（落盘目标 ≥2s）；默认用较长试听句
    text = (sample_text or "").strip() or build_voice_sample_text(prompt, display_name, short=False)
    resolved_speaker = (speaker or "").strip()
    if not resolved_speaker or resolved_speaker.startswith("S_"):
        resolved_speaker = infer_drama_speaker_from_prompt(
            prompt,
            character_name=display_name,
            asset_id=asset.id,
        )
    logger.info(
        "合成音色资产(TTS) project_id=%s asset_id=%s speaker=%s name=%s",
        project.id,
        asset.id,
        resolved_speaker,
        display_name,
    )
    audio_url = await ark.tts(
        text,
        resolved_speaker,
        project_id=project.id,
        shot_no=asset.id,
    )
    audio_url = finalize_voice_reference_url(
        audio_url or "",
        project_id=project.id,
        asset_id=asset.id,
    )
    asset.url = audio_url
    # 不把 mp3 写入 cover：避免 Seedance 把音色资产当成 reference_image
    cover = (asset.cover or "").split("?", 1)[0].strip().lower()
    if any(cover.endswith(ext) for ext in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".webm")):
        asset.cover = ""
    params: dict[str, Any] = dict(asset.params or {})
    params["voicePrompt"] = prompt
    params["sampleText"] = text
    params["speaker"] = resolved_speaker
    params["characterName"] = display_name
    if character_asset:
        params["sourceCharacterAssetId"] = character_asset.id
    gen = params.get("generation") if isinstance(params.get("generation"), dict) else {}
    params["generation"] = {**gen, "status": "done", "url": audio_url}
    asset.params = params
    await record_line(
        db,
        user_id=user.id,
        project_id=None,
        drama_project_id=project.id,
        billing_key="tts",
        model=(resolve_upstream_model("audio", None) or settings.model_audio),
        estimated=True,
        domain="drama",
    )
    await db.commit()
    await db.refresh(asset)
    return asset

