"""合成阶段 ffmpeg 字幕：口播须烧进画面，split 布局也不能省略。"""

from app.services.ffmpeg_compose import build_video_caption_vf


def test_split_layout_burns_narration_captions():
    """开源展示 split 布局仍要把旁白烧成底部字幕，不能只叠标题。"""
    vf = build_video_caption_vf(
        narration="搭物联网最耗时的，往往不是业务本身。",
        duration=12.0,
        w=720,
        h=1280,
        font=None,
        title="物模型",
        subtitle="统一管理设备",
        subtitle_layout="split",
    )
    assert "drawtext" in vf
    assert "搭物联网最耗时的" in vf
    assert "往往不是业务本身" in vf
    assert "enable='between(t\\," in vf
    assert ":y=70" in vf
    assert ":y=1176" in vf
    assert vf.count("drawtext=") >= 3


def test_strip_caption_punct_keeps_decimal_point():
    """数字中的小数点保留，句末句号仍去掉。"""
    from app.services.ffmpeg_compose import _strip_caption_punct

    assert _strip_caption_punct("去韩国做脸花了1.7万") == "去韩国做脸花了1.7万"
    assert _strip_caption_punct("涨了3.5%。真的.") == "涨了3.5真的"
