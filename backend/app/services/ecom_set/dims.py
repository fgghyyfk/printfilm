"""商品实际尺寸（厘米）：规整、给出图提示词的比例约束、尺寸标注图。

实拍是真实大小的商品放进场景，AI 出图不知道商品多大时，道具和手容易忽大忽小。
填了高 / 宽 / 厚后，每张图都会带上「商品真实尺寸 + 身边常见物件的参照」，让道具按真实比例搭配。
"""

from __future__ import annotations

# 不填尺寸的品类：丝巾按方巾尺寸单独处理，服饰按穿着
NO_DIMS_CATEGORIES = {"scarf", "apparel"}
MAX_CM = 300.0
# 常见参照物（名称, 高或长 cm），按尺寸找最接近的一个给模型做比例锚点
REFERENCES: tuple[tuple[str, float], ...] = (
    ("一枚硬币的直径", 2.5), ("一支口红", 8.5), ("一颗鸡蛋", 6.0), ("一只成年人的手掌宽度", 8.5),
    ("一张银行卡的长边", 8.6), ("一部手机", 15.0), ("一个成年人的手掌长度（手腕到指尖）", 18.0),
    ("一瓶 550ml 矿泉水", 22.0), ("一张 A4 纸的长边", 29.7), ("一瓶 750ml 红酒", 30.0),
    ("一个篮球的直径", 24.5), ("成年人的小臂长度", 45.0), ("一把椅子的座面高度", 45.0),
)
LABELS = {"h": "高", "w": "宽", "d": "厚"}


def normalize(value) -> dict:
    """规整为 {"h": 15.0, "w": 4.0, "d": 3.0}（厘米，可缺项）；非数字、非正数、超范围的项丢弃。"""
    out: dict = {}
    if not isinstance(value, dict):
        return out
    for k in LABELS:
        try:
            v = float(str(value.get(k) or "").strip().replace("cm", ""))
        except (TypeError, ValueError):
            continue
        if 0 < v <= MAX_CM:
            out[k] = round(v, 1)
    return out


def parse_form(h: str | float | None, w: str | float | None, d: str | float | None) -> dict:
    """表单里的高 / 宽 / 厚（字符串，可空）→ 规整后的尺寸。"""
    return normalize({"h": h, "w": w, "d": d})


def _num(v: float) -> str:
    return f"{v:g}"


def text(dims: dict | None) -> str:
    """「高 15 厘米 × 宽 4 厘米 × 厚 3 厘米」；没有尺寸返回空串。"""
    d = normalize(dims or {})
    return " × ".join(f"{LABELS[k]} {_num(d[k])} 厘米" for k in LABELS if k in d)


def short(dims: dict | None) -> str:
    """「15×4×3cm」，给标注与确认页。"""
    d = normalize(dims or {})
    vals = [_num(d[k]) for k in LABELS if k in d]
    return f"{'×'.join(vals)}cm" if vals else ""


def longest(dims: dict | None) -> float:
    """最长边（cm），没有尺寸时为 0。"""
    d = normalize(dims or {})
    return max(d.values()) if d else 0.0


def reference(dims: dict | None) -> str:
    """离最长边最接近的常见物件，如「和一部手机差不多」。"""
    size = longest(dims)
    if not size:
        return ""
    name, ref = min(REFERENCES, key=lambda r: abs(r[1] - size) / r[1])
    ratio = size / ref
    if 0.85 <= ratio <= 1.18:
        return f"大小和{name}差不多"
    return f"约为{name}的 {ratio:.1f} 倍"


def scale_note(dims: dict | None, *, ref: str = "composite") -> str:
    """出图提示词里的比例约束；没有尺寸返回空串。ref 为槽位类型（composite / hand / model / product）。"""
    body = text(dims)
    if not body:
        return ""
    near = reference(dims)
    head = f"商品真实尺寸：{body}" + (f"，{near}" if near else "")
    if ref == "hand":
        tail = hand_note(dims)
    elif ref == "model":
        tail = "商品与模特身体按真实比例呈现（成年女性身高约 165 厘米），不要把商品画得夸张地大或小"
    elif ref == "texture":
        return ""
    else:
        tail = ("画面里的道具、花材、水果、器皿与台面都按真实世界的大小和商品搭配，"
                "道具不要比实际大或小，不要出现比商品还大的小物件")
    return f"{head}。{tail}"


# 成年女性手部参考尺寸（厘米）：整只手长（手腕到中指指尖）、手掌宽、单根手指宽
HAND_LEN, PALM_W, FINGER_W = 17.0, 7.5, 1.6


def _times(ratio: float) -> str:
    """0.86 →「约八成半」这类说法太绕，统一写「约 0.9 倍」；接近 1 写「差不多长」。"""
    return "和整只手差不多长" if 0.9 <= ratio <= 1.1 else f"约为整只手长度的 {ratio:.1f} 倍"


def hand_note(dims: dict | None) -> str:
    """手持图的比例约束：把商品长、宽换算成手长与手指宽度，并按大小给出握法与构图要求。

    AI 画手持时常把商品画大（像握着一个大瓶子），只写「按真实比例」不够，需要给可对照的具体倍数。
    """
    d = normalize(dims or {})
    if not d:
        return ""
    vals = sorted(d.values(), reverse=True)
    long_side = vals[0]
    narrow = d.get("w") if "w" in d and d["w"] < long_side else (vals[1] if len(vals) > 1 else 0.0)
    parts = [f"手与商品必须按真实比例：成年女性整只手长（手腕到中指指尖）约 {HAND_LEN:g} 厘米、手掌宽约 {PALM_W:g} 厘米、"
             f"一根手指宽约 {FINGER_W:g} 厘米；商品最长边 {long_side:g} 厘米，{_times(long_side / HAND_LEN)}"]
    if narrow:
        share = narrow / PALM_W
        if share > 1.3:
            line = f"商品宽 {narrow:g} 厘米，约为手掌宽度的 {share:.1f} 倍"
        else:
            fingers = round(narrow / FINGER_W)
            line = f"商品宽 {narrow:g} 厘米，" + ("约一根手指宽" if fingers <= 1 else f"约等于 {fingers} 根手指并拢的宽度")
            if share < 0.95 and long_side >= 6:
                line += f"，只占手掌宽度的约 {share * 10:.0f} 成，手指能完全绕过商品握住，握住后商品明显比手掌窄"
        parts.append(line)
    if long_side < 6:
        parts.append("商品很小，用拇指和食指捏住，商品不要大过两节手指")
    elif long_side > 30:
        parts.append("商品比手大很多，用手扶着或托着商品，不要单手握住")
    parts.append("构图里手要完整入镜、能看到手腕，手和商品的大小关系一眼可辨；不要为了突出商品把商品画大，也不要把手缩小")
    return "；".join(parts)


def plan_note(dims: dict | None) -> str:
    """给规划模型的尺寸说明（写画面时不要把商品和道具的比例写反）。"""
    body = text(dims)
    if not body:
        return ""
    near = reference(dims)
    return f"商品实际尺寸：{body}" + (f"（{near}）" if near else "") + "，画面描述里的道具与商品要符合真实比例"
