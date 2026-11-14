"""时长标签后紧跟文字时不能丢内容：标签和文字拆成两行，正文一字不少。"""
import pytest

from app.services.drama.build_fragments import (
    normalize_duration_lines,
    parse_fragment_timed_blocks,
    prepare_fragment_content,
    repair_fragment_timed_layout,
)

HEADER = "【字幕：底部居中】\n【BGM：轻量配乐】\n"


@pytest.mark.parametrize("glue", ["", " ", "\t", "  "], ids=["none", "space", "tab", "spaces"])
@pytest.mark.parametrize("is_opening", [True, False])
def test_text_after_tag_keeps_block(glue, is_opening):
    """用户紧跟标签打字（@ 弹层插入标签后会自带一个空格）：这段和下面的内容都要保留。"""
    content = f"{HEADER}@duration:3{glue}我的字\n第一场\n@duration:3\n第二场\n@duration:3\n第三场"
    result = prepare_fragment_content(content, duration_sec=9, is_opening=is_opening)
    for text in ("我的字", "第一场", "第二场", "第三场"):
        assert text in result
    assert result.count("@duration:3") == 3
    assert "@duration:3\n我的字\n第一场" in result


def test_parse_keeps_all_blocks():
    """解析层：三段都在，时长不变。"""
    _, blocks = parse_fragment_timed_blocks("@duration:3 第一场\n@duration:2\n第二场\n@duration:4第三场")
    assert [dur for dur, _ in blocks] == [3, 2, 4]
    assert "第一场" in blocks[0][1] and "第二场" in blocks[1][1] and "第三场" in blocks[2][1]


def test_only_one_glued_block_is_kept():
    """只有一段时也不能被当成空内容返回。"""
    assert "只有这段" in repair_fragment_timed_layout("@duration:5 只有这段", duration_sec=5)


def test_consecutive_tags_on_one_line():
    """两个标签连在一行：都保留，文字归后一个。"""
    _, blocks = parse_fragment_timed_blocks("@duration:3@duration:2 文字")
    assert [dur for dur, _ in blocks] == [3, 2] and "文字" in blocks[1][1]


def test_asset_mention_after_tag_is_kept():
    """标签后紧跟资产引用：引用不能丢。"""
    result = prepare_fragment_content("@duration:3@asset:5 走进来\n@duration:3\n第二场", duration_sec=6, is_opening=True)
    assert "@asset:5 走进来" in result and "第二场" in result


@pytest.mark.parametrize("line", ["@duration:abc 我的字", "@duration: 我的字", "@duration:"])
def test_malformed_tag_line_does_not_swallow_following(line):
    """写坏的标签不是时长：不能把下面的段落一起吞掉。"""
    result = prepare_fragment_content(f"@duration:3\n第一场\n{line}\n后续文字\n@duration:3\n第二场",
                                      duration_sec=6, is_opening=True)
    assert "第一场" in result and "后续文字" in result and "第二场" in result
    if "我的字" in line:
        assert "我的字" in result


def test_zero_duration_block_text_is_kept():
    """0 秒标签没有意义，但它下面的文字是用户的内容。"""
    result = prepare_fragment_content("@duration:3\n第一场\n@duration:0\n零秒段文字\n@duration:3\n第二场",
                                      duration_sec=6, is_opening=True)
    assert "第一场" in result and "零秒段文字" in result and "第二场" in result


def test_inline_tag_after_text_is_kept():
    """标签在行中间（前面有文字）：内容同样不能丢。"""
    result = prepare_fragment_content("@duration:3\n前半句@duration:2后半句\n@duration:3\n第二场",
                                      duration_sec=8, is_opening=True)
    assert "前半句" in result and "后半句" in result and "第二场" in result


@pytest.mark.parametrize("content", [
    f"{HEADER}@duration:3\n第一场\n@duration:3\n第二场",
    "@duration:5\n第一行\n第二行",
])
def test_well_formed_content_is_unchanged(content):
    """格式正确的稿子不受影响。"""
    assert prepare_fragment_content(content, duration_sec=6, is_opening=True) == content


@pytest.mark.parametrize("line", ["@duration:15", "@duration:120", "  @duration:15  "])
def test_multi_digit_tag_is_not_split(line):
    """多位数时长是一个完整标签。"""
    assert normalize_duration_lines(line) == line.strip()


def test_multi_digit_tag_with_glued_text():
    """多位数标签后紧跟数字以外的文字也能拆对。"""
    assert normalize_duration_lines("@duration:15第一场") == "@duration:15\n第一场"


def test_normalization_is_idempotent():
    """规整结果再规整一次不变，否则每次保存都会改稿。"""
    once = prepare_fragment_content(f"{HEADER}@duration:3 我的字\n第一场\n@duration:3\n第二场", duration_sec=6, is_opening=True)
    assert prepare_fragment_content(once, duration_sec=6, is_opening=True) == once
