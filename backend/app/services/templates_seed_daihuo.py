"""商品带货模板：由「一图出套图」生成套图后接着出带货短视频（静图口播片）。

分类含「带货」→ 文案走广告法极限词替换模式；分镜画面直接复用套图底图，不再出图。
"""

from app.services.templates_seed_huoke import _HUOKE_SUB_SPLIT, HUOKE_IRON_RULES

DAIHUO_TEMPLATE_ID = "daihuo_product"

DAIHUO_TEMPLATES: list[dict] = [
    {
        "id": DAIHUO_TEMPLATE_ID,
        "name": "商品带货",
        "description": "上传一张商品图：先出整套电商主图/详情图，再用套图做画面，配口播与字幕出竖屏带货短视频。",
        "category": ["带货", "商业"],
        "preview_cover": f"/static/templates/covers/{DAIHUO_TEMPLATE_ID}.png",
        "style_prefix": "电商商品静帧，主体清晰，干净背景",
        "negative_prompt": "文字，水印，其他品牌 logo，商品变形",
        "default_ratio": "9:16",
        "shot_duration_min": 3,
        "shot_duration_max": 10,
        "llm_system_addon": HUOKE_IRON_RULES,
        "seedream_config": {"allow_source_names": True, "shot_count_min": 4, "shot_count_max": 8},
        "seedance_config": {"generate_audio": False},
        "audio_config": {"voice_preset": "urban_editorial", "bgm_mood": "轻快专业"},
        "subtitle_config": dict(_HUOKE_SUB_SPLIT),
        # 不在首页模板列表里抢位：从「商品带货」入口创建
        "sort_order": 900,
        "is_active": True,
        "is_premium": False,
    }
]
