"""验证显示画幅，不把编码尺寸、旋转和非方形像素混为一谈。"""
from fractions import Fraction

import pytest

from app.marketplace.drama_geometry import display_dimensions, display_ratio, fit_dimensions, validate_aspects


def _info(width, height, sar="1:1", rotation=0):
    """构造 FFprobe 的几何相关字段。"""
    return {"width": width, "height": height,
            "video": {"sample_aspect_ratio": sar, "side_data_list": [{"rotation": rotation}]}}


@pytest.mark.parametrize("rotation,dimensions,ratio", [
    (0, (768, 576), Fraction(4, 3)), (90, (576, 768), Fraction(3, 4)),
    (-90, (576, 768), Fraction(3, 4)), (180, (768, 576), Fraction(4, 3)),
])
def test_sar_is_applied_before_rotation(rotation, dimensions, ratio):
    info = _info(720, 576, "16:15", rotation)
    assert display_dimensions(info) == dimensions
    assert display_ratio(info) == ratio
    assert fit_dimensions(info, *dimensions) == dimensions


def test_small_pixel_rounding_and_same_ratio_different_sizes_are_allowed():
    infos = [_info(1920, 1080), _info(1280, 720), _info(854, 480)]
    assert validate_aspects(infos, [{"id": i} for i in (10, 11, 12)]) == [(1920, 1080), (1280, 720), (854, 480)]


def test_equivalent_sar_and_rotation_are_allowed():
    infos = [_info(720, 576, "16:15", 90), _info(576, 768)]
    assert validate_aspects(infos, [{"id": 10}, {"id": 11}]) == [(576, 768), (576, 768)]


def test_unsupported_rotation_does_not_silently_become_portrait():
    with pytest.raises(ValueError, match="MARKETPLACE_SOURCE_INCOMPATIBLE:分镜旋转角度不受支持"):
        display_ratio(_info(1920, 1080, rotation=45))


def test_subunit_sar_expands_height_without_lowering_source_resolution():
    info = _info(720, 480, "8:9", 90)
    assert display_dimensions(info) == (540, 720)
    assert display_ratio(info) == Fraction(3, 4)
