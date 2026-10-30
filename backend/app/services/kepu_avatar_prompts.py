"""数字人口播的提示词：定妆照、场景图、分段文案。纯函数，便于单测与批量复用。

项目上的几个开关决定提示词：
- avatar_look_style：形象风格（含「保持原样」：直接用上传照片里的人、衣服和场景，只去字幕水印、轻度美颜）
- avatar_framing：waist 腰部以上 | chest 胸像近景（口播博主式）
- avatar_scene_mode：per_segment 每段换场景 | fixed 全片同一场景
- avatar_script_style：kepu 科普讲解 | opinion 观点口播（个人 IP，标题拆两行钩子）
"""
from __future__ import annotations

import re

# 画风：写实 / 3D 卡通 / 日系动漫。场景图沿用定妆照的画风
RENDER_STYLES: dict[str, str] = {
    "photo": "photorealistic portrait photography, natural skin texture",
    "3d": ("high-quality 3D animated movie character style: stylized cartoon proportions, a slightly larger head "
           "and expressive big eyes, soft rounded features, smooth stylized skin, soft cinematic lighting"),
    "anime": ("clean modern anime illustration style: cel shading, crisp line art, large bright eyes, "
              "soft pastel colors"),
}

# 定妆照风格：id → (中文名, 服装/造型, 画风)。original 不换装，直接沿用原照片
ORIGINAL_LOOK = "original"
LOOK_STYLES: dict[str, tuple[str, str, str]] = {
    ORIGINAL_LOOK: ("保持原样（写实）", "", "photo"),
    "cartoon3d": ("3D 卡通", "a cute casual outfit: a soft pastel hoodie or sweater, relaxed and playful", "3d"),
    "cartoon_scientist": ("卡通小科学家", "a cute oversized white lab coat open over a colorful T-shirt", "3d"),
    "anime": ("日系动漫", "a casual light sweater with a cute small hair clip, relaxed and friendly", "anime"),
    "casual": ("清爽休闲（写实）", "a clean, bright casual outfit (light knit top or simple T-shirt)", "photo"),
    "school": ("校服学生（写实）", "a tidy school uniform with a collared shirt, student look", "photo"),
    "lab": ("科学小研究员（写实）", "a white lab coat over a light shirt, curious scientist look", "photo"),
    "host": ("知识主播（写实）", "smart business-casual clothing like a TV science show host", "photo"),
    "outdoor": ("户外探索（写实）", "an outdoor explorer outfit: light jacket, small backpack straps", "photo"),
}
DEFAULT_LOOK_STYLE = "cartoon3d"

# 美颜：瘦脸、柔和下颌线、皮肤干净、眼睛有神，但仍认得出是本人
_BEAUTY = (
    "Apply a flattering beauty retouch: a slimmer, more delicate face with a softer V-shaped jawline, smooth clear "
    "skin, bright lively eyes and a sweet friendly look, while the person stays clearly recognizable."
)
_LIGHT_BEAUTY = (
    "Apply only a light, natural beauty retouch (smoother skin, slightly slimmer face, brighter eyes) so they look "
    "their best but are unmistakably the same person."
)

# 构图：对口型要求正脸、闭嘴、脸部清晰、手不挡脸
FRAMINGS: dict[str, str] = {
    "waist": ("Upper body from the waist up, centered, the face takes about one quarter of the frame width, "
              "empty hands relaxed below the chest, no props"),
    "chest": ("Chest-up medium close-up like a vlogger talking to a phone camera, centered, the face takes about "
              "one third of the frame width with some space above the head, hands out of frame, no props"),
}
_FRAME_TAIL = (
    "Facing the camera and looking at the viewer, mouth gently closed with a warm natural expression, face clearly "
    "visible and sharp, vertical 9:16 portrait. No text, no captions, no watermark, no logo."
)


def talking_frame(framing: str | None) -> str:
    """对口型构图要求（未知构图按腰部以上）。"""
    return f"{FRAMINGS.get(framing or '', FRAMINGS['waist'])}. {_FRAME_TAIL}"


def look_render(style: str | None) -> str:
    """定妆照风格对应的画风；未知或空（直接用原照片）按写实。"""
    return LOOK_STYLES[style][2] if style in LOOK_STYLES else "photo"


def look_prompt(style: str, framing: str | None = "waist") -> str:
    """定妆照提示词。

    original：沿用原照片的人、衣服、发型和场景，去掉字幕/贴纸/水印，轻度美颜，按构图重新取景并补齐到 9:16；
    其它风格：只保留人物辨识度，美颜瘦脸，衣服、姿势、背景重新生成（卡通风格画成对应画风的角色）。
    """
    frame = talking_frame(framing)
    if style == ORIGINAL_LOOK:
        return (
            "Use the reference photo as the base. Keep the same person, the same outfit, hairstyle, accessories and "
            "the same background setting and lighting. Remove any overlaid text, captions, subtitles, title bars, "
            "stickers, logos or watermarks and naturally restore what is underneath. "
            f"{_LIGHT_BEAUTY} Recompose the shot: {frame} If the original is not tall enough, naturally extend the "
            f"background and clothing to fill the vertical frame. Art style: {RENDER_STYLES['photo']}."
        )
    _, outfit, render = LOOK_STYLES.get(style, LOOK_STYLES[DEFAULT_LOOK_STYLE])
    if render == "photo":
        identity = ("Create a new photo of the SAME person as in the reference image. Keep their identity: facial "
                    "features, eye shape, hairstyle and hair color, skin tone and apparent age.")
    else:
        identity = ("Turn the person in the reference image into a cartoon character who is still clearly "
                    "recognizable as them: keep their hairstyle and hair color, eye shape, skin tone and age.")
    return (
        f"{identity} {_BEAUTY} Dress them in {outfit}. Plain soft light-colored studio background, even soft "
        f"front lighting. Art style: {RENDER_STYLES[render]}. {frame}"
    )


def scene_prompt(scene: str, render: str = "photo", framing: str | None = "waist") -> str:
    """场景图提示词：人物（脸、发型、服装、画风）完全不变，只把背景与光线换成该场景。"""
    scene = (scene or "").strip() or "a bright, clean educational studio"
    return (
        "Keep the character from the reference image exactly the same: same face, hairstyle, outfit, body "
        f"proportions and art style. Change only the background and lighting to this scene: {scene}. The background "
        "should clearly illustrate the scene and stay slightly soft so the character stands out, lighting on the "
        f"character matches the scene. Art style for the whole image: {RENDER_STYLES.get(render, RENDER_STYLES['photo'])}. "
        f"{talking_frame(framing)}"
    )


def split_highlights(raw: str | list | None) -> list[str]:
    """把重点文字统一成列表：去空、去重、每个最多 8 字、最多 3 个。"""
    items = raw if isinstance(raw, list) else re.split(r"[|｜、,，]", str(raw or ""))
    out: list[str] = []
    for item in items:
        text = re.sub(r"\s+", "", str(item or ""))[:8]
        if text and text not in out:
            out.append(text)
    return out[:3]


_STYLE_BRIEF: dict[str, str] = {
    "kepu": """你是儿童/大众科普短视频编剧，为「真人数字人出镜口播」写分段脚本。
- 口语化、有画面感，像在跟观众聊天；第一段开头要有吸引人的提问或反常识点
- 每段讲一个小点，口播 35~75 字，段与段之间自然衔接
- 标题 title ≤12 字；title2 留空字符串""",
    "opinion": """你是短视频个人 IP 的口播编剧，为「真人出镜观点口播」写稿，像知识博主对着手机镜头分享行业观点。
- 第一人称、口语化、有态度、像朋友聊天；开头 3 秒抛出反常识观点或痛点作为钩子
- 层层递进：钩子 → 原因或案例 → 方法或建议 → 收尾金句，最后一句引导互动（如「你怎么看？评论区聊聊」）
- 每段 30~70 字，段与段之间自然衔接
- 标题拆成两行钩子：title 第一行 ≤10 字（抛出观点或对比），title2 第二行 ≤8 字（点出关键），
  例如 title「长寿行业不怕没机会」、title2「怕选错平台」
- 不夸大承诺，不涉及医疗疗效，不出现具体机构名""",
}


def script_system(style: str | None, scene_mode: str | None) -> str:
    """按文案风格与场景模式拼系统提示词（输出 JSON 结构随场景模式变化）。"""
    brief = _STYLE_BRIEF.get(style or "", _STYLE_BRIEF["kepu"])
    keywords = "- 每段给 1~3 个重点词（每个不超过 6 个字），是这一段最该被记住的概念或数字，必须原样出现在该段口播里"
    if scene_mode == "fixed":
        scene_rule = "- 全片只用一个出镜场景（人物在这个场景里对着镜头讲），用英文描述背景环境，不要描述人物"
        schema = ('{"title": "标题第一行", "title2": "标题第二行", "scene": "English background description", '
                  '"segments": [{"title": "小标题(≤10字)", "narration": "口播", "keywords": ["重点词"]}]}')
    else:
        scene_rule = ("- 每段给一个与内容强相关、能直观帮助理解的出镜场景（人物站在这个场景前讲解），"
                      "用英文描述背景环境，不要描述人物")
        schema = ('{"title": "标题第一行", "title2": "标题第二行", "segments": [{"title": "小标题(≤10字)", '
                  '"narration": "口播", "scene": "English background description", "keywords": ["重点词"]}]}')
    return f"{brief}\n{scene_rule}\n{keywords}\n只输出 JSON：{schema}"


def script_user_prompt(source_text: str, source_type: str, seg_range: tuple[int, int]) -> str:
    """拼文案请求：主题模式自由创作；口播稿模式只切分、不改写原文。"""
    lo, hi = seg_range
    if source_type == "script":
        return (f"下面是已经写好的口播稿，请按意思切成 {lo}~{hi} 段，口播必须一字不改地使用原文（可去掉段落符号），"
                f"再为每段补小标题、场景和重点词。\n\n口播稿：\n{source_text}")
    return f"主题：{source_text}\n请写 {lo}~{hi} 段。"


def normalize_segments(data: dict) -> list[dict]:
    """校验并清洗模型返回的分段，丢弃没有口播的段；段内无场景时用全片场景。"""
    fallback_scene = str(data.get("scene") or "").strip()[:600]
    segs: list[dict] = []
    for seg in data.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        narration = str(seg.get("narration") or "").strip()
        if not narration:
            continue
        keywords = [k for k in split_highlights(seg.get("keywords")) if k in narration] \
            or split_highlights(seg.get("keywords"))[:1]
        segs.append({
            "title": str(seg.get("title") or "").strip()[:20],
            "narration": narration,
            "scene": str(seg.get("scene") or "").strip()[:600] or fallback_scene,
            "keywords": keywords,
        })
    if not segs:
        raise RuntimeError("文案生成失败：模型没有返回有效的分段")
    return segs
