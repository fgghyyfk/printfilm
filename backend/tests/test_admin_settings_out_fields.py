"""管理端「运行参数」等页依赖的配置字段必须出现在 /api/admin/settings/models 响应里（缺了前端会白屏）。"""
from app.schemas_settings import AdminModelSettingsOut

# admin/src/components/settings/RuntimeSettingsPanel.tsx 直接读取并 .trim() / 比较的字段
RUNTIME_PANEL_FIELDS = (
    "ark_image_size", "ark_video_resolution", "ark_video_ratio",
    "seedance_duration_min", "seedance_duration_max",
    "ark_video_poll_interval", "ark_video_poll_timeout",
    "pipeline_image_concurrency", "pipeline_video_concurrency", "pipeline_audio_concurrency",
    "task_runtime_max_concurrency", "task_user_max_concurrency",
    "task_gpu_max_concurrency", "task_gpu_user_max_concurrency", "task_poll_max_concurrency",
)


def test_runtime_panel_fields_present_in_out_model():
    """响应模型包含运行参数页用到的全部字段。"""
    missing = [f for f in RUNTIME_PANEL_FIELDS if f not in AdminModelSettingsOut.model_fields]
    assert not missing, f"AdminModelSettingsOut 缺字段：{missing}"


def test_secret_fields_default_empty():
    """密钥字段默认空串，只通过 has_* 判断是否已配置。"""
    out = AdminModelSettingsOut()
    assert out.ark_api_key == "" and out.openai_api_key == ""
