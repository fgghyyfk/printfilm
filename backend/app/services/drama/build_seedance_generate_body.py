"""组装漫剧分镜提交 Seedance 的多模态请求体。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TypedDict

from app.config import get_settings
from app.services.logical_model_router import resolve_logical_model_id, resolve_upstream_model
from app.models_drama import DramaAsset
from app.services.drama.build_fragments import rewrite_dialogue_action_lines
from app.services.drama.fragment_content_duration import (
    replace_duration_mentions_with_time_ranges,
    resolve_seedance_duration_from_content,
)
from app.services.drama.image_styles import (
    STYLE_BOARD_PROMPT_HINT,
    resolve_image_style_prompt,
)
from app.services.seedance_segments import (
    VOICE_CUE_PREFIX_RE,
    classify_voice_body,
    is_production_meta_line,
    rewrite_misclassified_visual_voice_lines,
    script_has_narration_cue,
    strip_character_intro_cues,
    strip_model_burn_subtitle_cues,
)

ASSET_MENTION_TOKEN_PATTERN = re.compile(r"@asset:(\d+)")
# 仅压缩行内空白，保留换行以便 Seedance 按时间轴段落演绎
INLINE_WHITESPACE_PATTERN = re.compile(r"[^\S\n]+")

SEEDANCE_VISUAL_STYLE_SECTION_INTRO = (
    "【强制约束：视频画面风格】全片画面必须严格遵循以下风格描述，"
    "严禁偏离、弱化或混用其他画风与镜头美学："
)
# 提交角色/旁白试听为 Seedance reference_audio，并写入音色强制约束。
SEEDANCE_ATTACH_REFERENCE_AUDIO = True
SEEDANCE_CHARACTER_VOICE_SECTION_HEADER = (
    "【强制约束：角色音色】以下角色说话的音色、语气、节奏与发声质感必须与对应参考音频严格一致，"
    "语速自然偏慢、吐字清晰，严禁加速赶词、替换、混用其他声线或自行改写："
)
SEEDANCE_NARRATION_VOICE_SECTION_HEADER = (
    "【强制约束：旁白音色】以下旁白说话的音色、语气、节奏与发声质感必须与对应参考音频严格一致，"
    "语速自然偏慢、吐字清晰，严禁加速赶词、替换、混用其他声线或自行改写："
)
SEEDANCE_CHARACTER_APPEARANCE_SECTION_HEADER = (
    "【强制约束：角色形象】以下角色的面容、体型、发型、服饰与整体气质必须与对应参考图严格一致，"
    "严禁换脸、形象漂移或重绘为其他人物："
)
SEEDANCE_SCENE_SECTION_HEADER = (
    "【强制约束：场景】以下场景的空间结构、环境陈设、光影氛围必须与对应参考图严格一致，"
    "严禁替换为其他场景或大幅偏离参考画面："
)
SEEDANCE_MUSIC_SECTION_HEADER = (
    "【强制约束：参考音乐】以下音频是全片背景音乐参考，不是角色对白音色。"
    "配乐的旋律、节奏与情绪必须贴合对应参考音频；无台词镜头保持闭嘴，禁止把这段音乐念成对白："
)
SEEDANCE_PROP_SECTION_HEADER = (
    "【强制约束：道具】以下道具的外形、材质与关键细节必须与对应参考图严格一致，"
    "严禁替换为其他物品或丢失标志性特征："
)


class BuildSeedanceGenerateBodyInput(TypedDict, total=False):
    content: str | None
    reference: list[dict[str, Any]] | None
    model_id: str | None
    aspect_ratio: str | None
    resolution: str | None
    video_style_id: str | None
    duration_fallback: int | None
    # 上一镜尾帧公网/本地 URL；有参考媒体时作 reference_image（不可与 first_frame 混用）
    continuity_first_frame_url: str | None
    # 画风板公网 URL（角色/场景图之后、衔接尾帧之前）
    style_board_url: str | None
    # True=模型烧录字幕；False=后期叠字（禁止画面内字幕）
    burn_subtitles: bool
    # True=角色身旁人物介绍叠字；False=禁止人物介绍字卡
    character_intro: bool


# 兼容历史布尔 / 字符串，解析分集 params 开关
def _resolve_episode_bool_flag(
    params: dict[str, Any] | None,
    *,
    mode_key: str,
    enabled_key: str,
    on_mode: str,
    off_mode: str,
    default: bool = True,
) -> bool:
    raw = params if isinstance(params, dict) else {}
    mode = raw.get(mode_key)
    if mode == off_mode:
        return False
    if mode == on_mode:
        return True
    enabled = raw.get(enabled_key)
    if enabled is None:
        return default
    if isinstance(enabled, str):
        return enabled.strip().lower() not in {"0", "false", "no", "off", ""}
    if isinstance(enabled, (int, float)):
        return enabled != 0
    return bool(enabled)


# 从分集 params 解析是否由模型烧录字幕（默认后期拼接 → False）
def resolve_episode_burn_subtitles(params: dict[str, Any] | None) -> bool:
    return _resolve_episode_bool_flag(
        params,
        mode_key="subtitleMode",
        enabled_key="subtitleEnabled",
        on_mode="model",
        off_mode="post",
        default=False,
    )


# 从分集 params 解析是否注入人物介绍叠字（默认关闭 → False）
def resolve_episode_character_intro(params: dict[str, Any] | None) -> bool:
    return _resolve_episode_bool_flag(
        params,
        mode_key="characterIntroMode",
        enabled_key="characterIntroEnabled",
        on_mode="model",
        off_mode="off",
        default=False,
    )


@dataclass
class SeedanceReferenceFile:
    asset_id: int
    url: str


@dataclass
class SeedanceReferenceCatalog:
    images: list[SeedanceReferenceFile] = field(default_factory=list)
    audios: list[SeedanceReferenceFile] = field(default_factory=list)
    image_index_by_asset_id: dict[int, int] = field(default_factory=dict)
    audio_index_by_asset_id: dict[int, int] = field(default_factory=dict)
    videos: list[SeedanceReferenceFile] = field(default_factory=list)
    video_index_by_asset_id: dict[int, int] = field(default_factory=dict)


# 将 ORM 资产转为 Seedance 引用 payload
def drama_asset_to_payload(asset: DramaAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "type": asset.type,
        "assetType": asset.asset_type,
        "name": asset.name,
        "cover": asset.cover,
        "url": asset.url,
        "params": asset.params or {},
    }


def _voice_audio_url_from_value(voice_audio: Any) -> str | None:
    """从 voiceAudio 字段取值：支持 {url}/previewUrl 对象，或历史/OSS 回填后的纯字符串 URL。"""
    if isinstance(voice_audio, str):
        url = voice_audio.strip()
        return url or None
    if isinstance(voice_audio, dict):
        url = voice_audio.get("url") or voice_audio.get("previewUrl")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


# 读取角色绑定的参考音频 URL（兼容 voiceAudio 与 canvas.voiceAudio）
def read_asset_voice_audio_url(params: Any) -> str | None:
    if not isinstance(params, dict):
        return None

    direct = _voice_audio_url_from_value(params.get("voiceAudio"))
    if direct:
        return direct

    canvas = params.get("canvas")
    if isinstance(canvas, dict):
        nested = _voice_audio_url_from_value(canvas.get("voiceAudio"))
        if nested:
            return nested

    return None


# 音频后缀：禁止当作 reference_image（音色 cover 常被写成 mp3）
_AUDIO_URL_SUFFIXES = (
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".flac",
    ".opus",
    ".webm",
)


def _looks_like_audio_url(url: str) -> bool:
    """URL 路径是否像音频文件（忽略 query）。"""
    path = (url or "").split("?", 1)[0].strip().lower()
    return any(path.endswith(suffix) for suffix in _AUDIO_URL_SUFFIXES)


# 解析资产可用的图片 URL（绝不返回音频；voice/audio 资产不进参考图）
def resolve_reference_image_url(asset: dict[str, Any]) -> str | None:
    cover = (asset.get("cover") or "").strip()
    url = (asset.get("url") or "").strip()
    asset_type = str(asset.get("assetType") or "").strip().lower()
    category_type = str(asset.get("type") or "").strip().lower()

    # 音色/音频节点：cover 可能被写成试听 URL，绝不能进 image_url
    if asset_type in {"audio", "voice", "video"} or category_type in {"audio", "voice", "narration", "video"}:
        return None

    candidate = ""
    if asset_type == "image" or category_type in ("character", "scene", "prop", "material", "image"):
        candidate = cover or url
    else:
        candidate = cover

    if not candidate or _looks_like_audio_url(candidate):
        return None
    return candidate


# 解析写入提示词的资产名称
def resolve_asset_prompt_name(asset: dict[str, Any], fallback: str) -> str:
    params = asset.get("params") if isinstance(asset.get("params"), dict) else {}
    entity = ""
    if isinstance(params, dict):
        entity = str(params.get("entityName") or params.get("name") or "").strip()
    return entity or (asset.get("name") or "").strip() or fallback


def _voice_binding_label(params: Any) -> str:
    """从 voiceAudio.label 取可读音色/角色名（画布误名时的回退）。"""
    if not isinstance(params, dict):
        return ""
    for key in ("voiceAudio",):
        binding = params.get(key)
        if isinstance(binding, dict):
            label = str(binding.get("label") or "").strip()
            if label and label not in {"音色", "试听", "音频"}:
                return label
    canvas = params.get("canvas")
    if isinstance(canvas, dict):
        binding = canvas.get("voiceAudio")
        if isinstance(binding, dict):
            label = str(binding.get("label") or "").strip()
            if label and label not in {"音色", "试听", "音频"}:
                return label
    return ""


def resolve_character_prompt_name(asset: dict[str, Any]) -> str:
    """写入提示词的角色名；纯数字/单字名回退音色标签，再回退角色#id。"""
    name = resolve_asset_prompt_name(asset, "角色")
    compact = re.sub(r"\s+", "", name)
    if not compact or compact.isdigit() or len(compact) <= 1:
        voice_label = _voice_binding_label(asset.get("params"))
        if voice_label and not voice_label.isdigit() and len(re.sub(r"\s+", "", voice_label)) > 1:
            return voice_label
        asset_id = asset.get("id")
        return f"角色{asset_id}" if asset_id is not None else "角色"
    return name


def resolve_narration_prompt_name(asset: dict[str, Any]) -> str:
    return resolve_asset_prompt_name(asset, "旁白")


def resolve_scene_prompt_name(asset: dict[str, Any]) -> str:
    return resolve_asset_prompt_name(asset, "场景")


def resolve_other_asset_prompt_name(asset: dict[str, Any]) -> str:
    return resolve_asset_prompt_name(asset, f"资产#{asset.get('id')}")


def _prepare_voice_script(
    content: str | None,
    reference: list[dict[str, Any]] | None = None,
) -> str:
    """提交前脚本：自由口播拆行、拆舞台指示、纠正空镜误标、角色 VO 改对白。"""
    normalized = normalize_canvas_freeform_speech(content, reference)
    normalized = rewrite_dialogue_action_lines(normalized)
    return rewrite_misclassified_visual_voice_lines(normalized)


# 画布自由提示词里的口播迹象（非「角色名：台词」格式）
_FREEFORM_SPEECH_RE = re.compile(
    r"(?:说(?:道|着)?[：:\s]|\{[^{}\n]{2,}\}|张嘴|开口说话|对白|台词|口播|念出|大声说)",
)
# 画布「他说："台词"」→ 拆成画面行 + 对白行，便于绑定 @音频
# 动词前禁止 据/听/传/再/小 等，避免「据说/听说/小说」误拆
_FREEFORM_SAID_QUOTE_RE = re.compile(
    r"^(?P<head>.*?)(?P<verb>(?<![据听传再小])(?:他|她|它)?说(?:道|着)?)\s*[：:]\s*"
    r"[「\"“'](?P<quote>[^」\"”']{1,200})[」\"”']"
    r"(?P<tail>.*)$"
)
# 画布/即梦「说{台词}」：Seedance 原生 {}=对白，须拆出【对白】，禁止留在【画面·无配音】里
_FREEFORM_BRACE_SPEECH_RE = re.compile(
    r"^(?P<head>.*?)(?P<verb>(?<![据听传再小])(?:他|她|它)?说(?:道|着)?)\s*"
    r"\{(?P<quote>[^{}\n]{1,200})\}"
    r"(?P<tail>.*)$"
)
# 画面行内残留的 说{…} / {…}（抽完对白后清掉，避免与无配音冲突）
_RESIDUAL_SAY_BRACE_RE = re.compile(
    r"(?:(?<![据听传再小])(?:他|她|它)?说(?:道|着)?)?\s*\{([^{}\n]{1,200})\}"
)
# 行内裸 {台词}（即梦语法，无「说」动词）
_FREEFORM_BARE_BRACE_RE = re.compile(
    r"^(?P<head>.*?)\{(?P<quote>[^{}\n]{2,200})\}(?P<tail>.*)$"
)
# 花括号内容像画面描述而非台词（含景别/资产 id 等）
_BRACE_VISUAL_HINT_RE = re.compile(
    r"(?:^@asset:\d+|^\d{2,}\b|中景|近景|远景|特写|全景|门口|背景是|镜头|日景|夜景|站在|正面朝向)"
)


def _script_has_freeform_speech(script: str) -> bool:
    """画布常见写法：说 xxx / {台词} / 张嘴说话。"""
    return bool(_FREEFORM_SPEECH_RE.search(script or ""))


def _brace_content_is_visual(quote: str) -> bool:
    """花括号内是画面描述（非口播台词）时返回 True。"""
    text = (quote or "").strip()
    if not text:
        return True
    if _BRACE_VISUAL_HINT_RE.search(text):
        return True
    return False


def _strip_residual_say_braces(text: str) -> str:
    """去掉行内残留 说{…}/{…}（调用方已抽出台词或判定为画面噪声）。"""
    cleaned = _RESIDUAL_SAY_BRACE_RE.sub("", text or "")
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip(" ，,。．")


def _primary_character_mention(script: str, reference: list[dict[str, Any]] | None) -> str:
    """取正文里第一个角色 @asset，或唯一角色资产名，供自由口播行挂说话人。"""
    names_by_id: dict[int, str] = {}
    character_assets: list[dict[str, Any]] = []
    for asset in reference or []:
        if asset.get("type") != "character":
            continue
        character_assets.append(asset)
        names_by_id[int(asset["id"])] = resolve_character_prompt_name(asset)
    for token in ASSET_MENTION_TOKEN_PATTERN.findall(script or ""):
        mapped = names_by_id.get(int(token))
        if mapped:
            return f"@asset:{token}"
    if len(character_assets) == 1:
        only = character_assets[0]
        return f"@asset:{int(only['id'])}"
    if character_assets:
        return resolve_character_prompt_name(character_assets[0])
    return "角色"


def _emit_visual_and_dialogue(
    *,
    head: str,
    quote: str,
    tail: str,
    speaker_src: str,
    reference: list[dict[str, Any]] | None,
    visual_prefix: str,
) -> list[str]:
    """拼画面行 + 对白行；画面行清掉残留花括号。"""
    speaker = _primary_character_mention(speaker_src, reference)
    visual = " ".join(part for part in (head, tail) if part).strip()
    visual = _strip_residual_say_braces(visual)
    rows: list[str] = []
    if visual:
        if not visual.startswith("【画面") and not visual.startswith("【空镜"):
            visual = f"{visual_prefix}{visual}"
        rows.append(visual)
    rows.append(f"【对白·慢速清晰】{speaker}：{quote}")
    return rows


def normalize_canvas_freeform_speech(
    content: str | None,
    reference: list[dict[str, Any]] | None,
) -> str:
    """画布自由句：把「他说："…" / 说{…}」拆成【画面】+【对白】，并清掉画面行残留 {}。"""
    from app.services.seedance_segments import VISUAL_PREFIX

    out: list[str] = []
    for raw in (content or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("@duration:") or is_production_meta_line(line):
            out.append(raw)
            continue
        if line.startswith("【对白") or line.startswith("【旁白") or line.startswith("【内心"):
            out.append(raw)
            continue

        match = _FREEFORM_SAID_QUOTE_RE.match(line)
        if match:
            head = (match.group("head") or "").strip().rstrip("，,。． ")
            quote = (match.group("quote") or "").strip()
            tail = (match.group("tail") or "").strip()
            if quote:
                out.extend(
                    _emit_visual_and_dialogue(
                        head=head,
                        quote=quote,
                        tail=tail,
                        speaker_src=line,
                        reference=reference,
                        visual_prefix=VISUAL_PREFIX,
                    )
                )
                continue

        brace = _FREEFORM_BRACE_SPEECH_RE.match(line)
        if brace:
            head = (brace.group("head") or "").strip().rstrip("，,。． ")
            quote = (brace.group("quote") or "").strip()
            tail = (brace.group("tail") or "").strip()
            if quote and not _brace_content_is_visual(quote):
                out.extend(
                    _emit_visual_and_dialogue(
                        head=head,
                        quote=quote,
                        tail=tail,
                        speaker_src=line,
                        reference=reference,
                        visual_prefix=VISUAL_PREFIX,
                    )
                )
                continue
            # 画面型 {…}：只删花括号，不升格为对白
            cleaned = _strip_residual_say_braces(line)
            if cleaned:
                out.append(cleaned)
            elif head:
                out.append(head)
            else:
                out.append(raw)
            continue

        # 裸 {台词}：升格对白；画面型 {…} 只删除
        if "{" in line and "}" in line:
            bare = _FREEFORM_BARE_BRACE_RE.match(line)
            if bare:
                quote = (bare.group("quote") or "").strip()
                head = (bare.group("head") or "").strip().rstrip("，,。． ")
                tail = (bare.group("tail") or "").strip()
                if quote and not _brace_content_is_visual(quote):
                    out.extend(
                        _emit_visual_and_dialogue(
                            head=head,
                            quote=quote,
                            tail=tail,
                            speaker_src=line,
                            reference=reference,
                            visual_prefix=VISUAL_PREFIX,
                        )
                    )
                    continue
            cleaned = _strip_residual_say_braces(line)
            if cleaned and cleaned != line:
                out.append(cleaned)
                continue

        out.append(raw)
    return "\n".join(out)


def collect_speaking_character_names(
    script: str,
    reference: list[dict[str, Any]] | None,
) -> set[str]:
    """从对白/旁白/独白行收集本镜开口的角色名（含 @asset 引用）。

    分镜剧本用「角色名：台词」；自由画布常用「说 …」，后者在标号对白扫不到时
    回退：有口播迹象则把正文中出现的角色名（或唯一角色）视为开口。
    """
    names_by_id: dict[int, str] = {}
    aliases: list[str] = []
    character_assets: list[dict[str, Any]] = []
    for asset in reference or []:
        if asset.get("type") != "character":
            continue
        character_assets.append(asset)
        prompt_name = resolve_character_prompt_name(asset)
        names_by_id[int(asset["id"])] = prompt_name
        aliases.append(prompt_name)
        raw_name = str(asset.get("name") or "").strip()
        if raw_name:
            aliases.append(raw_name)
    aliases = sorted({name for name in aliases if name}, key=len, reverse=True)
    spoken: set[str] = set()
    for raw in (script or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("@duration:") or is_production_meta_line(line):
            continue
        if line.startswith("【画面") or line.startswith("【空镜"):
            continue
        body = VOICE_CUE_PREFIX_RE.sub("", line).strip()
        # 已标【对白/内心】的行直接视为开口，避免 @asset: 等形态 classify 失败丢音色
        if line.startswith("【对白") or line.startswith("【内心"):
            kind = "dialogue" if line.startswith("【对白") else "inner"
        else:
            kind = classify_voice_body(body)
        if kind not in {"dialogue", "inner"}:
            continue
        for token in ASSET_MENTION_TOKEN_PATTERN.findall(body):
            mapped = names_by_id.get(int(token))
            if mapped:
                spoken.add(mapped)
        for name in aliases:
            if _name_is_token_prefix(body, name):
                spoken.add(name)
                break

    if spoken or not character_assets or not _script_has_freeform_speech(script):
        return spoken

    # 自由画布：正文提到的角色视为开口；只连了一个角色则默认他开口
    blob = script or ""
    for asset in character_assets:
        prompt_name = resolve_character_prompt_name(asset)
        raw_name = str(asset.get("name") or "").strip()
        if (prompt_name and prompt_name in blob) or (raw_name and raw_name in blob):
            if prompt_name:
                spoken.add(prompt_name)
            if raw_name:
                spoken.add(raw_name)
    if spoken:
        return spoken
    if len(character_assets) == 1:
        only = character_assets[0]
        prompt_name = resolve_character_prompt_name(only)
        raw_name = str(only.get("name") or "").strip()
        if prompt_name:
            spoken.add(prompt_name)
        if raw_name:
            spoken.add(raw_name)
    return spoken


def _should_attach_reference_audio(
    asset: dict[str, Any],
    *,
    filter_audio: bool,
    speaking: set[str],
    has_narration: bool,
) -> bool:
    """未开口角色不挂音色；无第三人称旁白则不挂旁白音色。"""
    if not filter_audio:
        return True
    kind = asset.get("type")
    if kind == "character":
        prompt_name = resolve_character_prompt_name(asset)
        raw_name = str(asset.get("name") or "").strip()
        return prompt_name in speaking or raw_name in speaking
    if kind == "narration":
        return has_narration
    return True


# 从引用资产构建参考图/音频目录
def build_seedance_reference_catalog(
    reference: list[dict[str, Any]] | None,
    script: str | None = None,
) -> SeedanceReferenceCatalog:
    catalog = SeedanceReferenceCatalog()
    seen_image_asset_ids: set[int] = set()
    seen_audio_asset_ids: set[int] = set()
    seen_video_asset_ids: set[int] = set()
    filter_audio = script is not None
    voice_script = _prepare_voice_script(script, reference) if filter_audio else ""
    speaking = (
        collect_speaking_character_names(voice_script, reference) if filter_audio else set()
    )
    has_narration = script_has_narration_cue(voice_script) if filter_audio else True

    for asset in reference or []:
        asset_id = int(asset["id"])
        if asset.get("type") == "video" or asset.get("assetType") == "video":
            video_url = str(asset.get("url") or "").strip()
            if video_url and asset_id not in seen_video_asset_ids:
                seen_video_asset_ids.add(asset_id)
                catalog.videos.append(SeedanceReferenceFile(asset_id=asset_id, url=video_url))
            continue
        image_url = resolve_reference_image_url(asset)

        if image_url and asset_id not in seen_image_asset_ids:
            seen_image_asset_ids.add(asset_id)
            catalog.images.append(SeedanceReferenceFile(asset_id=asset_id, url=image_url))

        if SEEDANCE_ATTACH_REFERENCE_AUDIO and asset.get("type") in {"character", "narration"}:
            if not _should_attach_reference_audio(
                asset,
                filter_audio=filter_audio,
                speaking=speaking,
                has_narration=has_narration,
            ):
                continue
            voice_audio_url = read_asset_voice_audio_url(asset.get("params"))
            if voice_audio_url and asset_id not in seen_audio_asset_ids:
                seen_audio_asset_ids.add(asset_id)
                catalog.audios.append(SeedanceReferenceFile(asset_id=asset_id, url=voice_audio_url))
        elif SEEDANCE_ATTACH_REFERENCE_AUDIO and asset.get("type") in {"audio", "voice"}:
            # 画布独立音频/音色节点：直接用资产 url 作为 reference_audio
            audio_url = str(asset.get("url") or asset.get("cover") or "").strip()
            if audio_url and asset_id not in seen_audio_asset_ids:
                seen_audio_asset_ids.add(asset_id)
                catalog.audios.append(SeedanceReferenceFile(asset_id=asset_id, url=audio_url))

    catalog.image_index_by_asset_id = {
        item.asset_id: index + 1 for index, item in enumerate(catalog.images)
    }
    catalog.audio_index_by_asset_id = {
        item.asset_id: index + 1 for index, item in enumerate(catalog.audios)
    }
    catalog.video_index_by_asset_id = {
        item.asset_id: index + 1 for index, item in enumerate(catalog.videos)
    }
    return catalog


_KIND_ZH = {
    "character": "角色",
    "scene": "场景",
    "image": "场景",
    "material": "场景",
    "prop": "道具",
    "narration": "旁白",
}

# 画布「新图片」等无分类图：按场景挂强制约束，避免只进 content[] 却无场景约束
_SCENE_LIKE_TYPES = frozenset({"scene", "image", "material"})


def _asset_matches_reference_category(asset: dict[str, Any], category_type: str) -> bool:
    """判断资产是否属于某类参考索引（场景含 type=image/material）。"""
    kind = str(asset.get("type") or "").lower()
    if category_type == "scene":
        return kind in _SCENE_LIKE_TYPES
    return kind == category_type


# 按 content[] 下标生成可读标签（与 build_seedance_content_items 顺序一致）
def describe_seedance_content_slots(
    reference: list[dict[str, Any]] | None,
    continuity_first_frame_url: str | None = None,
    *,
    has_text: bool = True,
    style_board_url: str | None = None,
    content: str | None = None,
) -> list[str]:
    catalog = build_seedance_reference_catalog(reference, script=content)
    asset_by_id = {
        int(asset["id"]): asset
        for asset in (reference or [])
        if isinstance(asset, dict) and asset.get("id") is not None
    }
    labels: list[str] = []
    if has_text:
        labels.append("分镜文案")
    for image in catalog.images:
        asset = asset_by_id.get(image.asset_id) or {}
        kind = str(asset.get("type") or "").lower()
        kind_zh = _KIND_ZH.get(kind, "参考图")
        name = str(asset.get("name") or "").strip() or f"资产#{image.asset_id}"
        labels.append(f"{kind_zh}「{name}」")
    board = (style_board_url or "").strip() if catalog.images else ""
    if board:
        labels.append("画风板")
    for audio in catalog.audios:
        asset = asset_by_id.get(audio.asset_id) or {}
        kind = str(asset.get("type") or "").lower()
        kind_zh = _KIND_ZH.get(kind, "音色")
        name = str(asset.get("name") or "").strip() or f"资产#{audio.asset_id}"
        labels.append(f"{kind_zh}音色「{name}」")
    for video in catalog.videos:
        asset = asset_by_id.get(video.asset_id) or {}
        labels.append(f"参考视频「{asset.get('name') or video.asset_id}」")
    if (continuity_first_frame_url or "").strip():
        labels.append("上一镜尾帧")
    return labels


# 正文中的 @asset 替换文案
def format_body_asset_mention(
    name: str,
    image_index: int | None,
    audio_index: int | None = None,
    video_index: int | None = None,
) -> str:
    """正文资产提及：角色可同时挂 @图片N 与 @音频N，便于模型对齐口令。"""
    parts: list[str] = []
    if image_index is not None:
        parts.append(f"@图片{image_index}/参考图{image_index}")
    if audio_index is not None:
        parts.append(f"@音频{audio_index}/参考音频{audio_index}")
    if video_index is not None:
        parts.append(f"@视频{video_index}/参考视频{video_index}")
    if parts:
        return f"{name}（{'，'.join(parts)}）"
    return name


def _asset_prompt_name_for_mention(asset: dict[str, Any] | None) -> str:
    """按资产类型解析写入提示词的显示名。"""
    if not asset:
        return ""
    category = str(asset.get("type") or "").lower()
    if category == "character":
        return resolve_character_prompt_name(asset)
    if category in _SCENE_LIKE_TYPES:
        return resolve_scene_prompt_name(asset)
    return resolve_other_asset_prompt_name(asset)


_NAME_TOKEN_BOUNDARIES = frozenset(" \t，,、；;：:。．.!！?？）)]】」』\"'（([【「『“”和与跟及同在到向对把被让给的地得了着过也又再就都还从带")


def _name_is_token_suffix(text: str, name: str) -> bool:
    """text 是否以独立的 name 结尾（避免 大禹 吃掉 禹）。"""
    if not name or not text.endswith(name):
        return False
    prefix = text[: -len(name)]
    return (not prefix) or prefix[-1] in _NAME_TOKEN_BOUNDARIES


def _name_is_token_prefix(text: str, name: str) -> bool:
    """text 是否以独立的 name 开头（避免 禹王 被当成 禹）。"""
    if not name or not text.startswith(name):
        return False
    rest = text[len(name) :]
    return (not rest) or rest[0] in _NAME_TOKEN_BOUNDARIES


def _mention_audio_index(
    asset: dict[str, Any] | None,
    catalog: SeedanceReferenceCatalog,
) -> int | None:
    """角色/旁白音色与画布独立音频都要写成 @音频N，否则上游不使用该参考音频。"""
    if not asset or asset.get("id") is None:
        return None
    kind = str(asset.get("type") or "").lower()
    if kind not in {"character", "narration", "audio", "voice"}:
        return None
    return (getattr(catalog, "audio_index_by_asset_id", {}) or {}).get(int(asset["id"]))


# 将单个 @asset 占位符替换为正文描述
def replace_asset_mention_token(
    asset_id: int,
    asset_by_id: dict[int, dict[str, Any]],
    catalog: SeedanceReferenceCatalog,
) -> str:
    asset = asset_by_id.get(asset_id)
    if not asset:
        return ""
    image_index = (getattr(catalog, "image_index_by_asset_id", {}) or {}).get(int(asset["id"]))
    audio_index = _mention_audio_index(asset, catalog)
    return format_body_asset_mention(
        _asset_prompt_name_for_mention(asset),
        image_index,
        audio_index,
        catalog.video_index_by_asset_id.get(asset_id),
    )


def replace_asset_mentions_without_duplicate_names(
    text: str,
    asset_by_id: dict[int, dict[str, Any]],
    catalog: SeedanceReferenceCatalog,
) -> str:
    """把 @asset:id 换成「名称（参考图N）」，吞掉前后已经写过的同名。"""
    pieces: list[str] = []
    pos = 0
    for match in ASSET_MENTION_TOKEN_PATTERN.finditer(text):
        pieces.append(text[pos : match.start()])
        asset_id = int(match.group(1))
        token = replace_asset_mention_token(asset_id, asset_by_id, catalog)
        name = _asset_prompt_name_for_mention(asset_by_id.get(asset_id))
        image_index = (getattr(catalog, "image_index_by_asset_id", {}) or {}).get(asset_id)
        audio_index = (getattr(catalog, "audio_index_by_asset_id", {}) or {}).get(asset_id)
        if name and _name_is_token_suffix("".join(pieces).rstrip(), name):
            # 名字已写在 @asset 前时只保留括号索引，避免「妖皇妖皇」
            parts: list[str] = []
            if image_index is not None:
                parts.append(f"@图片{image_index}/参考图{image_index}")
            if _mention_audio_index(asset_by_id.get(asset_id), catalog) is not None:
                parts.append(f"@音频{audio_index}/参考音频{audio_index}")
            video_index = catalog.video_index_by_asset_id.get(asset_id)
            if video_index is not None:
                parts.append(f"@视频{video_index}/参考视频{video_index}")
            token = f"（{'，'.join(parts)}）" if parts else ""
        end = match.end()
        skip = 0
        if name:
            rest = text[end:]
            stripped = rest.lstrip()
            ws = len(rest) - len(stripped)
            if _name_is_token_prefix(stripped, name):
                skip = ws + len(name)
        pieces.append(token)
        pos = end + skip
    pieces.append(text[pos:])
    return "".join(pieces)


# 组装画面风格声明块；有画风板时强调只借气质、禁止抄主体
def build_visual_style_section(
    video_style_id: str | None,
    *,
    has_style_board: bool = False,
) -> str | None:
    style_prompt = resolve_image_style_prompt(video_style_id)
    if not style_prompt:
        return None
    extra = f"\n{STYLE_BOARD_PROMPT_HINT}" if has_style_board else ""
    return f"{SEEDANCE_VISUAL_STYLE_SECTION_INTRO}\n{style_prompt}{extra}"


def build_reference_index_section(
    reference: list[dict[str, Any]] | None,
    category_type: str,
    index_map: dict[int, int],
    header: str,
    index_label: str,
    resolve_name: Any,
    *,
    image_index_map: dict[int, int] | None = None,
) -> str | None:
    lines: list[str] = []
    seen_asset_ids: set[int] = set()

    for asset in reference or []:
        asset_id = int(asset["id"])
        if not _asset_matches_reference_category(asset, category_type) or asset_id in seen_asset_ids:
            continue
        seen_asset_ids.add(asset_id)
        reference_index = index_map.get(asset_id)
        if reference_index is None:
            continue
        # 方舟官方口令用 @音频N / @图片N；音色绑定强调「图片N中的角色用音频M说」
        if index_label in {"参考音频", "参考图"}:
            at_token = "@音频" if index_label == "参考音频" else "@图片"
            if index_label == "参考音频":
                who = resolve_name(asset)
                image_index = (image_index_map or {}).get(asset_id)
                if image_index is not None:
                    lines.append(
                        f"@图片{image_index}中的角色「{who}」必须使用{at_token}{reference_index}"
                        f"（{index_label}{reference_index}）的音色说出全部对白；"
                        f"音色、语气、节奏与发声质感严格一致，语速自然偏慢、吐字清晰，严禁换声线"
                    )
                else:
                    lines.append(
                        f"角色「{who}」必须使用{at_token}{reference_index}"
                        f"（{index_label}{reference_index}）的音色说出全部对白；"
                        f"音色、语气、节奏与发声质感严格一致，语速自然偏慢、吐字清晰，严禁换声线"
                    )
            else:
                lines.append(
                    f"{resolve_name(asset)}：{at_token}{reference_index}"
                    f"（{index_label}{reference_index}）"
                )
        else:
            lines.append(f"{resolve_name(asset)}：{index_label}{reference_index}")

    if not lines:
        return None
    return "\n".join([header, *lines])


def build_music_reference_section(
    reference: list[dict[str, Any]] | None,
    catalog: SeedanceReferenceCatalog,
) -> str | None:
    """独立音频节点写成背景音乐口令；角色音色仍走对白约束，避免互相覆盖。"""
    if not SEEDANCE_ATTACH_REFERENCE_AUDIO:
        return None
    lines: list[str] = []
    seen: set[int] = set()
    for asset in reference or []:
        if str(asset.get("type") or "").lower() not in {"audio", "voice"}:
            continue
        asset_id = int(asset["id"])
        if asset_id in seen:
            continue
        seen.add(asset_id)
        audio_index = (catalog.audio_index_by_asset_id or {}).get(asset_id)
        if audio_index is None:
            continue
        name = resolve_other_asset_prompt_name(asset)
        lines.append(
            f"背景音乐使用@音频{audio_index}（参考音频{audio_index}）「{name}」，"
            "贯穿有配乐的镜头，不要当作任何人声对白"
        )
    if not lines:
        return None
    return "\n".join([SEEDANCE_MUSIC_SECTION_HEADER, *lines])


# 将分镜脚本转为正文提示词
def _normalize_body_whitespace(text: str) -> str:
    lines = [
        INLINE_WHITESPACE_PATTERN.sub(" ", raw).strip()
        for raw in text.replace("\r\n", "\n").split("\n")
    ]
    return "\n".join(line for line in lines if line).strip()


_ORPHAN_ASSET_MENTION_LINE_RE = re.compile(
    r"^(?P<name>[^（(\n【]{1,40})"
    r"（(?P<refs>(?:@图片\d+/参考图\d+|@音频\d+/参考音频\d+|参考图\d+|参考音频\d+)"
    r"(?:，[^）]*)?)）\s*$"
)


def _merge_orphan_asset_mention_lines(text: str) -> str:
    """单独一行的「角色名（@图片N…）」并入下一行画面/对白，避免游离提及打断口令。"""
    raw_lines = (text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    index = 0
    while index < len(raw_lines):
        line = raw_lines[index].strip()
        match = _ORPHAN_ASSET_MENTION_LINE_RE.match(line) if line else None
        if match and index + 1 < len(raw_lines):
            nxt = raw_lines[index + 1].strip()
            name = match.group("name").strip()
            if nxt.startswith(("【画面", "【对白", "【旁白", "【内心")) and name:
                if name not in nxt:
                    cue = re.match(r"^(【[^】]+】)\s*(.*)$", nxt)
                    if cue:
                        nxt = f"{cue.group(1)}{name} {cue.group(2)}".rstrip()
                    else:
                        nxt = f"{name} {nxt}"
                out.append(nxt)
                index += 2
                continue
        out.append(raw_lines[index])
        index += 1
    return "\n".join(out)


def build_seedance_body_text(
    content: str | None,
    reference: list[dict[str, Any]] | None,
    catalog: SeedanceReferenceCatalog,
) -> str:
    # 自由口播拆行、拆舞台指示、纠正误标为对白/旁白的空镜画面行
    normalized = normalize_canvas_freeform_speech(content, reference)
    normalized = rewrite_dialogue_action_lines(normalized)
    normalized = rewrite_misclassified_visual_voice_lines(normalized)
    asset_by_id = {int(asset["id"]): asset for asset in (reference or [])}
    replaced = replace_duration_mentions_with_time_ranges(normalized)
    replaced = replace_asset_mentions_without_duplicate_names(replaced, asset_by_id, catalog)
    replaced = _merge_orphan_asset_mention_lines(replaced)
    return _normalize_body_whitespace(replaced)


def build_seedance_prompt_text(
    content: str | None,
    reference: list[dict[str, Any]] | None,
    catalog: SeedanceReferenceCatalog | None = None,
    video_style_id: str | None = None,
    *,
    burn_subtitles: bool = False,
    character_intro: bool = False,
    has_style_board: bool = False,
) -> str:
    # 提交前：自由口播拆行、拆舞台指示、纠正空镜误标，保证强制约束与正文一致
    normalized = normalize_canvas_freeform_speech(content, reference)
    normalized = rewrite_dialogue_action_lines(normalized)
    normalized = rewrite_misclassified_visual_voice_lines(normalized)
    if not burn_subtitles:
        # 后期模式：去掉字幕 cue /「同步字幕」前缀，避免模型仍按字烧屏
        normalized = strip_model_burn_subtitle_cues(normalized)
    if not character_intro:
        normalized = strip_character_intro_cues(normalized)
    resolved_catalog = catalog or build_seedance_reference_catalog(reference, script=normalized)
    # Submitted prompts contain the selected visual style and storyboard body only.
    # Reference images/audio stay in content[], without extra mandatory instruction paragraphs.
    sections = [
        build_visual_style_section(video_style_id, has_style_board=has_style_board),
        build_seedance_body_text(normalized, reference, resolved_catalog),
    ]
    return "\n\n".join(section for section in sections if section)


# 从引用资产组装 Seedance content 多模态数组
def build_seedance_content_items(
    content: str | None,
    reference: list[dict[str, Any]] | None,
    video_style_id: str | None = None,
    continuity_first_frame_url: str | None = None,
    *,
    burn_subtitles: bool = False,
    character_intro: bool = False,
    style_board_url: str | None = None,
) -> list[dict[str, Any]]:
    catalog = build_seedance_reference_catalog(reference, script=content)
    # 无角色/场景图时不挂画风板，避免板子变成唯一画面参考
    board = (style_board_url or "").strip() if catalog.images else ""
    prompt_text = build_seedance_prompt_text(
        content,
        reference,
        catalog,
        video_style_id,
        burn_subtitles=burn_subtitles,
        character_intro=character_intro,
        has_style_board=bool(board),
    )
    items: list[dict[str, Any]] = []

    continuity = (continuity_first_frame_url or "").strip()
    has_reference_media = bool(catalog.images or catalog.audios or catalog.videos)

    if prompt_text:
        items.append({"type": "text", "text": prompt_text})

    for image in catalog.images:
        items.append(
            {"type": "image_url", "image_url": {"url": image.url}, "role": "reference_image"}
        )

    if board:
        items.append(
            {"type": "image_url", "image_url": {"url": board}, "role": "reference_image"}
        )

    for audio in catalog.audios:
        items.append(
            {"type": "audio_url", "audio_url": {"url": audio.url}, "role": "reference_audio"}
        )
    for video in catalog.videos:
        items.append(
            {"type": "video_url", "video_url": {"url": video.url}, "role": "reference_video"}
        )

    if continuity:
        if has_reference_media:
            items.append(
                {
                    "type": "image_url",
                    "image_url": {"url": continuity},
                    "role": "reference_image",
                }
            )
        else:
            # 无参考媒体时可用 first_frame，并在外层省略 ratio
            items.append(
                {
                    "type": "image_url",
                    "image_url": {"url": continuity},
                    "role": "first_frame",
                }
            )

    return items


def resolve_seedance_model_endpoint(model_id: str | None) -> str:
    """解析上游视频模型 id；Seedance 旧连字符统一成 TokenFree 点分目录名。"""
    from app.services.media_model_presets import resolve_preset_alias
    from app.services.tokenfree_pricing import (
        canonicalize_channel_model_id,
        video_catalog_id,
    )

    settings = get_settings()
    raw = resolve_preset_alias((model_id or "").strip()) or (model_id or "").strip()
    wanted = canonicalize_channel_model_id(raw) if raw else ""
    logical_id = resolve_logical_model_id("video", raw or model_id)
    routed = resolve_upstream_model("video", logical_id or (raw if raw else None))
    if routed:
        got = canonicalize_channel_model_id(routed)
        # 用户点名 Seedance 档位时，禁止路由静默换成另一档（mini→2.5）
        if wanted and "seedance" in wanted.lower() and got != wanted:
            if video_catalog_id(wanted) == wanted or "mini" in wanted or "fast" in wanted:
                return wanted
        return got
    if not raw:
        return canonicalize_channel_model_id(
            settings.model_video or (settings.model_video_2 or "").strip()
        )
    # 用户点名的 Seedance 档位必须原样提交；禁止用站点默认 model_video（常为 2.0）盖掉 2.5
    if wanted and "seedance" in wanted.lower():
        return wanted
    return wanted or canonicalize_channel_model_id(raw)


def resolve_seedance_ratio(aspect_ratio: str | None) -> str:
    # 与漫剧默认竖屏一致；缺失时不得回落到横屏 16:9
    return (aspect_ratio or "9:16").strip() or "9:16"


def resolve_seedance_resolution(
    resolution: str | None,
    *,
    model_id: str | None = None,
) -> str:
    """解析并按模型能力钳制清晰度（MiniMax 提交小写 480p/768p）。"""
    from app.services.seedance_resolutions import format_video_resolution_for_upstream

    return format_video_resolution_for_upstream(model_id, resolution)


# 将分镜参数转为 Seedance 请求体
def build_seedance_generate_body(input_params: BuildSeedanceGenerateBodyInput) -> dict[str, Any]:
    from app.services.seedance_resolutions import clamp_video_duration

    content = input_params.get("content")
    fallback = int(input_params.get("duration_fallback") or 8)
    continuity = (input_params.get("continuity_first_frame_url") or "").strip() or None
    reference = input_params.get("reference")
    catalog = build_seedance_reference_catalog(reference, script=content)
    # 仅「纯首帧、无参考媒体」时省略 ratio；混用参考时必须保留 ratio、且尾帧用 reference_image
    style_board_url = (input_params.get("style_board_url") or "").strip() or None
    if not catalog.images:
        style_board_url = None
    use_first_frame_mode = bool(continuity) and not (catalog.images or catalog.audios or catalog.videos)
    # 缺省与产品一致：后期叠字，不烧模型字幕 / 不叠人物介绍
    burn_subtitles = input_params.get("burn_subtitles")
    if burn_subtitles is None:
        burn_subtitles = False
    character_intro = input_params.get("character_intro")
    if character_intro is None:
        character_intro = False

    mid = input_params.get("model_id")
    raw_duration = resolve_seedance_duration_from_content(content, fallback=fallback)
    body: dict[str, Any] = {
        "model": resolve_seedance_model_endpoint(mid),
        "content": build_seedance_content_items(
            content,
            reference,
            input_params.get("video_style_id"),
            continuity_first_frame_url=continuity,
            burn_subtitles=bool(burn_subtitles),
            character_intro=bool(character_intro),
            style_board_url=style_board_url,
        ),
        "duration": clamp_video_duration(mid, raw_duration),
        "resolution": resolve_seedance_resolution(
            input_params.get("resolution"),
            model_id=mid,
        ),
        "watermark": False,
        # Seedance 原生配音；字幕/人物介绍叠字由对应开关控制提示词
        "generate_audio": True,
        "return_last_frame": True,
    }
    if not use_first_frame_mode:
        body["ratio"] = resolve_seedance_ratio(input_params.get("aspect_ratio"))
    return body
