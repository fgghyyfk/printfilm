"""广告法极限词库：分类词表 + 检测/替换。

依据《广告法》第九条（不得使用「国家级」「最高级」「最佳」等用语）、第二十八条（虚假广告）、
第十七条（非药品不得涉及疾病治疗功能）及常见市场监管执法口径整理。

两档处理：
- block：明确违规，自动替换为通顺说法或删除；
- warn：需看语境或需资质证明的词，保留原文，提示人工确认。

用法：clean, hits = check_text(text)；hits 里每项有 word / category / level。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

BLOCK = "block"
WARN = "warn"


@dataclass(frozen=True)
class Hit:
    """一次命中：原词、分类、处理档位。"""

    word: str
    category: str
    level: str


# 分类 → [(词, 替换词)]；替换词为空串表示删除。同一类内长词在前
BLOCK_WORDS: dict[str, list[tuple[str, str]]] = {
    "最高级": [
        ("最好用", "好用"), ("最实用", "实用"), ("最受欢迎", "受欢迎"), ("最划算", "划算"),
        ("最低价", "实惠价"), ("最便宜", "实惠"), ("最高级", "高品质"), ("最先进", "先进"),
        ("最新技术", "新技术"), ("最新款", "新款"), ("最新", "新款"), ("最佳", "优选"),
        ("最好", "优选"), ("最优", "优选"), ("最强", "强劲"), ("最大程度", "充分"),
        ("最高端", "高端"), ("最舒适", "舒适"), ("最安全", "安全"),
    ],
    "绝对化": [
        ("100%", ""), ("百分之百", ""), ("百分百", ""), ("绝对", ""), ("完美", "出色"),
        ("极致", "出色"), ("极品", "精品"), ("顶级", "高端"), ("顶尖", "出色"), ("巅峰", ""),
        ("终极", ""), ("无敌", ""), ("至尊", "尊享"), ("永久", "持久"), ("万能", "多用"),
        ("零风险", ""), ("无副作用", ""), ("无与伦比", ""), ("史无前例", ""), ("前无古人", ""),
        ("空前绝后", ""), ("独一无二", "独特"), ("无可替代", ""), ("100%纯", "纯"),
    ],
    "第一/唯一": [
        ("全国第一", ""), ("全网第一", ""), ("销量第一", "热销"), ("排名第一", ""),
        ("行业第一", ""), ("第一品牌", ""), ("第一", "领先"), ("唯一", ""), ("首个", ""),
        ("首款", "新款"), ("首家", ""), ("首选", "优选"), ("首发", "新品"), ("独家", ""),
        ("冠军", ""), ("销量王", "热销"), ("领导品牌", ""), ("领袖品牌", ""), ("全网", ""),
        ("全球领先", "领先"), ("全国领先", "领先"), ("遥遥领先", "领先"), ("史上", ""),
    ],
    "权威背书": [
        ("国家级", ""), ("世界级", ""), ("国际级", ""), ("宇宙级", ""), ("国家免检", ""),
        ("国家领导人", ""), ("特供", ""), ("专供", ""), ("国宴", ""), ("驰名商标", ""),
        ("质量免检", ""), ("免检", ""), ("国家推荐", ""), ("专家推荐", ""), ("权威认证", ""),
        ("老字号", ""), ("祖传", ""), ("秘方", ""),
    ],
    "虚假稀缺/诱导": [
        ("仅此一次", ""), ("错过不再", ""), ("再不抢就没了", ""), ("抢疯了", ""),
        ("史上最低", ""), ("全网最低", ""), ("跳楼价", "优惠价"), ("清仓价", "优惠价"),
        ("亏本", ""), ("免费领", ""), ("点击领奖", ""), ("恭喜获奖", ""), ("秒杀全网", ""),
        ("万人疯抢", "热销"), ("售罄", ""),
    ],
    "迷信": [
        ("招财进宝", ""), ("旺夫", ""), ("辟邪", ""), ("转运", ""), ("开光", ""),
        ("保平安", ""), ("化煞", ""), ("增强第六感", ""),
    ],
    # 化妆品功效宣称：需功效评价报告才能写（《化妆品功效宣称评价规范》），默认替换成不含功效承诺的说法
    "化妆品功效": [
        ("增强肌肤屏障", "呵护肌肤"), ("增强皮肤屏障", "呵护肌肤"), ("强韧肌肤屏障", "呵护肌肤"),
        ("修护肌肤屏障", "呵护肌肤"), ("修复肌肤屏障", "呵护肌肤"), ("修护皮肤屏障", "呵护肌肤"),
        ("修复皮肤屏障", "呵护肌肤"), ("修护屏障", "呵护肌肤"), ("修复屏障", "呵护肌肤"),
        ("屏障修护", "肌肤呵护"), ("屏障修复", "肌肤呵护"), ("修复受损肌肤", "呵护肌肤"),
        ("修复肌肤", "呵护肌肤"), ("修复皮肤", "呵护肌肤"), ("再生肌肤", "呵护肌肤"),
        ("细胞再生", ""), ("激活细胞", ""), ("抗过敏", ""), ("抗敏", ""), ("脱敏", ""),
        ("零刺激", "温和"), ("无刺激", "温和"), ("医美级", ""), ("药妆", ""), ("械字号", ""),
    ],
    "医疗功效": [
        ("药到病除", ""), ("包治百病", ""), ("根治", "改善"), ("治愈", ""), ("治疗", ""),
        ("疗效", "效果"), ("防癌", ""), ("抗癌", ""), ("降血压", ""), ("降血糖", ""),
        ("降血脂", ""), ("消炎", ""), ("抗炎", ""), ("排毒", ""), ("增强免疫力", ""), ("医用级", ""),
        ("药用", ""), ("处方", ""),
    ],
}

# 需人工确认（多为需检测报告/资质的功效词）：保留原文，只提示
WARN_WORDS: dict[str, list[str]] = {
    "功效宣称": [
        "杀菌", "抑菌", "除菌", "除螨", "防霉", "抗病毒", "除甲醛", "净化空气",
        "抗皱", "祛斑", "美白", "祛痘", "去黑头", "生发", "防脱", "瘦身", "减肥", "丰胸",
        "助眠", "护眼", "防近视", "补钙", "改善睡眠",
    ],
    "材质/数据宣称": ["纯棉", "真丝", "桑蚕丝", "纯金", "纯银", "零添加", "无添加", "有机", "进口"],
    "时效/价格": ["限时", "限量", "特价", "原价", "最后一天"],
}

# 白名单：这些词里的「最」「一级」等不算违规
WHITELIST = (
    "最近", "最后", "最终", "最初", "最少", "最多可", "最长可", "最高可", "最后一天",
    "能效一级", "一级能效", "第一次", "第一步", "第一天", "第一眼", "第一口", "第一时间",
    "第一集", "第一章", "第一季", "第一个", "第一位", "第一层", "第一代", "第一部分",
    "历史上", "转运蛋白", "转运站", "永久冻土", "冠军赛",
)

# 正则规则：英文/数字类绝对化表述
REGEX_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(?i)\bno\.?\s*1\b|\btop\s*1\b|\btop\s*one\b|\bnumber\s*one\b|\bbest\b"), "", "第一/唯一"),
    (re.compile(r"[1-9]\d{2,}%"), "", "绝对化"),
]
# 兜底：「最 + 中文」的比较级（白名单除外）
MOST_RE = re.compile(r"最(?=[一-龥])")


# 词表展开成按长度倒序的 (词, 替换, 分类)，保证长词先匹配；按自定义词缓存，后台改词后自动生效
@lru_cache(maxsize=8)
def _block_entries_for(extra: tuple[str, ...]) -> tuple[tuple[str, str, str], ...]:
    rows = [(w, r, cat) for cat, pairs in BLOCK_WORDS.items() for w, r in pairs]
    rows += [(w, "", "自定义") for w in extra]
    return tuple(sorted(rows, key=lambda x: len(x[0]), reverse=True))


# 当前生效的拦截词条
def _block_entries() -> tuple[tuple[str, str, str], ...]:
    return _block_entries_for(tuple(_extra_words()))


# 读取配置里追加的自定义禁用词（逗号/顿号分隔）
def _extra_words() -> list[str]:
    try:
        from app.config import get_settings

        raw = get_settings().ad_banned_extra or ""
    except Exception:  # noqa: BLE001
        raw = ""
    return [w.strip() for w in re.split(r"[,，、;；\s]+", raw) if w.strip()]


# 清掉缓存（测试用；正常按自定义词自动失效）
def reload_words() -> None:
    _block_entries_for.cache_clear()


# 词库概览（给管理后台展示）：各分类词数与示例
def catalog() -> dict:
    return {
        "block": [{"category": c, "count": len(p), "examples": [w for w, _ in p[:8]]} for c, p in BLOCK_WORDS.items()],
        "warn": [{"category": c, "count": len(w), "examples": w[:8]} for c, w in WARN_WORDS.items()],
        "extra": _extra_words(),
    }


# 把白名单词暂时替换成占位符，避免被误伤
def _protect(text: str) -> tuple[str, dict[str, str]]:
    keep: dict[str, str] = {}
    for i, w in enumerate(WHITELIST):
        if w in text:
            token = f"\u0000{i}\u0000"
            keep[token] = w
            text = text.replace(w, token)
    return text, keep


# 还原白名单占位符
def _restore(text: str, keep: dict[str, str]) -> str:
    for token, w in keep.items():
        text = text.replace(token, w)
    return text


REPLACE = "replace"
REPORT = "report"


def check_text(text: str, *, mode: str = REPLACE) -> tuple[str, list[Hit]]:
    """检测并清洗一段文本，返回 (结果文本, 命中列表)。

    mode=replace：拦截档自动替换/删除（广告、带货文案用）；
    mode=report：只报告不改字（科普、剧情等非广告内容用，避免误改「第一次」「国家级保护区」等）。
    按行处理，保留换行与每行开头的脚本标记；warn 档在任何模式下都只记录。
    """
    if not text:
        return text or "", []
    hits: list[Hit] = []
    lines = [_check_line(line, hits, replace=(mode == REPLACE)) for line in text.split("\n")]
    return "\n".join(lines), hits


# 处理单行：替换/记录拦截词，再记录提醒词
def _check_line(line: str, hits: list[Hit], *, replace: bool) -> str:
    if not line.strip():
        return line
    out, keep = _protect(line)
    for word, repl, cat in _block_entries():
        if word in out:
            hits.append(Hit(word, cat, BLOCK))
            if replace:
                out = out.replace(word, repl)
    for pattern, repl, cat in REGEX_RULES:
        for m in pattern.findall(out):
            hits.append(Hit(m if isinstance(m, str) else m[0], cat, BLOCK))
        if replace:
            out = pattern.sub(repl, out)
    if MOST_RE.search(out):
        hits.append(Hit("最", "最高级", BLOCK))
        if replace:
            out = MOST_RE.sub("", out)
    out = _restore(out, keep)
    for cat, words in WARN_WORDS.items():
        for w in words:
            if w in out:
                hits.append(Hit(w, cat, WARN))
    if not replace or out == line:
        return line
    # 只清理替换留下的空白与多余标点，不动行首缩进与脚本标记
    lead = line[: len(line) - len(line.lstrip())]
    body = re.sub(r"[ \t]{2,}", " ", out.strip())
    body = re.sub(r"([，,、；;])\1+", r"\1", body)
    body = re.sub(r"^[，,、；;]+|[，,、；;]+$", "", body).strip()
    return lead + body


def summarize(hits: list[Hit], *, mode: str = "replace") -> list[str]:
    """把命中合并成给用户看的提示语（去重）。"""
    blocked = list(dict.fromkeys(h.word for h in hits if h.level == BLOCK))
    warned = list(dict.fromkeys(h.word for h in hits if h.level == WARN))
    msgs: list[str] = []
    if blocked and mode == REPLACE:
        msgs.append(f"已自动替换广告法极限词：{'、'.join(blocked)}")
    elif blocked:
        msgs.append(f"发现可能违反广告法的用语（未修改，如用于推广请调整）：{'、'.join(blocked)}")
    if warned:
        msgs.append(f"以下用语需有检测报告或资质证明，请人工确认：{'、'.join(warned)}")
    return msgs
