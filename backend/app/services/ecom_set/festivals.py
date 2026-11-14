"""电商图节日日历：全年 24 个营销节点（日期、提前量、文案语气、节日元素包、对应节日场景）。

农历节日与冬至按表换算（2026～2035，用 lunardate / sxtwl 核对过），超出范围时沿用最后一年的月日近似。
「临近」= 正在进行，或离开始不超过 SOON_DAYS 天：场景列表里排在最前，元素包按日期排序。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

SOON_DAYS = 60

FESTIVAL_TYPES: dict[str, str] = {
    "traditional": "传统节日",
    "promo": "电商大促",
    "gift": "浪漫礼赠",
    "season": "四季时令",
}

# 农历节日的公历日期：春节、元宵、端午、七夕、中秋
_LUNAR: dict[str, dict[int, tuple[int, int]]] = {
    "chunjie": {2026: (2, 17), 2027: (2, 6), 2028: (1, 26), 2029: (2, 13), 2030: (2, 3),
                2031: (1, 23), 2032: (2, 11), 2033: (1, 31), 2034: (2, 19), 2035: (2, 8)},
    "yuanxiao": {2026: (3, 3), 2027: (2, 20), 2028: (2, 9), 2029: (2, 27), 2030: (2, 17),
                 2031: (2, 6), 2032: (2, 25), 2033: (2, 14), 2034: (3, 5), 2035: (2, 22)},
    "duanwu": {2026: (6, 19), 2027: (6, 9), 2028: (5, 28), 2029: (6, 16), 2030: (6, 5),
               2031: (6, 24), 2032: (6, 12), 2033: (6, 1), 2034: (6, 20), 2035: (6, 10)},
    "qixi": {2026: (8, 19), 2027: (8, 8), 2028: (8, 26), 2029: (8, 16), 2030: (8, 5),
             2031: (8, 24), 2032: (8, 12), 2033: (8, 1), 2034: (8, 20), 2035: (8, 10)},
    "zhongqiu": {2026: (9, 25), 2027: (9, 15), 2028: (10, 3), 2029: (9, 22), 2030: (9, 12),
                 2031: (10, 1), 2032: (9, 19), 2033: (9, 8), 2034: (9, 27), 2035: (9, 16)},
    "dongzhi": {2026: (12, 22), 2027: (12, 22), 2028: (12, 21), 2029: (12, 21), 2030: (12, 22),
                2031: (12, 22), 2032: (12, 21), 2033: (12, 21), 2034: (12, 22), 2035: (12, 22)},
}


@dataclass(frozen=True)
class Festival:
    key: str
    name: str
    type: str
    rule: tuple            # ("solar", m, d) | ("lunar", key) | ("nth_sunday", m, n) | ("range", (m, d), (m, d)) | ("nianhuo",)
    lead: int              # 提前多少天进入备货期
    tail: int              # 节后还算进行中的天数
    tone: str              # 给规划模型的文案语气
    en: str                # 英文副标建议
    pack: tuple[str, ...]  # 节日元素包（元素 key，不含 el_ 前缀）
    scenes: tuple[str, ...]


def _f(key, name, type_, rule, lead, tail, tone, en, pack, scenes) -> Festival:
    return Festival(key, name, type_, rule, lead, tail, tone, en, tuple(pack.split()), tuple(scenes.split()))


FESTIVALS: tuple[Festival, ...] = (
    _f("yuandan", "元旦跨年", "traditional", ("solar", 1, 1), 15, 1, "跨年迎新，语气轻快闪耀，适合「新年新气象」「开启新一年」",
       "HELLO NEW YEAR", "gold_ribbon gold_sequin champagne sparkler gold_balloon confetti", "fest_yuandan"),
    _f("nianhuo", "年货节", "promo", ("nianhuo",), 0, 0, "年货节：囤年货、送礼、犒劳自己的语气，喜庆温暖",
       "NEW YEAR SHOPPING", "red_knit gift_stack red_berry mandarin red_velvet red_arch wood_block cherry",
       "fest_nianhuo food_festival"),
    _f("chunjie", "春节", "traditional", ("lunar", "chunjie"), 40, 7, "新春：新年祝福、团圆、送礼的语气，喜庆大气但不俗套",
       "HAPPY NEW YEAR", "chinese_knot red_envelope red_plum fu_paper persimmon mandarin red_knit gold_ingot mahjong lantern firecracker",
       "fest_chunjie fest_chunjie_cozy"),
    _f("yuanxiao", "元宵", "traditional", ("lunar", "yuanxiao"), 12, 0, "元宵：团圆、灯火、甜蜜的语气",
       "LANTERN FESTIVAL", "flower_lantern tangyuan lantern red_string mandarin red_plum", "fest_yuanxiao"),
    _f("valentine", "情人节", "gift", ("solar", 2, 14), 25, 0, "情人节：浪漫、心动、送给 TA 的礼物语气",
       "BE MY VALENTINE", "rose rose_bouquet heart_choco love_letter ribbon candle rose_petal pearl", "fest_valentine"),
    _f("women", "女神节", "gift", ("solar", 3, 8), 20, 0, "女神节：宠爱自己、自信优雅的语气",
       "LOVE YOURSELF", "pearl tulip crown silk rose_petal mirror", "fest_women"),
    _f("spring", "春日踏青", "season", ("range", (3, 10), (4, 30)), 15, 0, "春日：明媚、焕新、出门踏青的轻快语气",
       "HELLO SPRING", "cherry_blossom spring_blossom sprout picnic_cloth rattan_basket paper_butterfly tulip sunbeam", "fest_spring"),
    _f("labor", "五一出游", "season", ("solar", 5, 1), 20, 4, "五一：出游必备、轻装出发的语气",
       "TRAVEL ESSENTIALS", "summer_hat sunglasses travel_map film_camera dappled", "fest_labor"),
    _f("mother", "母亲节", "gift", ("nth_sunday", 5, 2), 20, 0, "母亲节：感恩、温柔、送给妈妈的语气",
       "FOR MOM WITH LOVE", "carnation greeting_card gift_box pearl tea_cup linen", "fest_mother"),
    _f("d520", "520", "gift", ("solar", 5, 20), 15, 0, "520：甜蜜告白、送礼的语气",
       "LOVE YOU", "heart_balloon heart rose_petal love_letter gift_box ribbon", "fest_520"),
    _f("children", "儿童节", "gift", ("solar", 6, 1), 15, 0, "儿童节：童趣、快乐、大朋友也过节的语气",
       "HAPPY CHILDREN'S DAY", "blocks balloon plush_bear candy confetti", "fest_children"),
    _f("d618", "618", "promo", ("solar", 6, 18), 25, 0, "618 年中大促：囤货、好物推荐、热闹的语气",
       "MID-YEAR SALE", "gift_stack shopping_bag balloon confetti ribbon round_plinth", "fest_618"),
    _f("duanwu", "端午", "traditional", ("lunar", "duanwu"), 20, 0, "端午：安康、清雅、东方的语气",
       "DRAGON BOAT FESTIVAL", "zongzi mugwort five_rope sachet bamboo_leaf dragon_boat", "fest_duanwu"),
    _f("father", "父亲节", "gift", ("nth_sunday", 6, 3), 15, 0, "父亲节：沉稳、感谢、送给爸爸的语气",
       "FOR DAD", "leather watch whisky_glass books old_wood", "fest_father"),
    _f("summer", "夏日清凉", "season", ("range", (6, 21), (8, 31)), 15, 0, "夏日：清凉、清爽、活力的语气",
       "SUMMER VIBES", "ice watermelon lemonade lemon_slice splash palm_shadow summer_hat", "fest_summer"),
    _f("qixi", "七夕", "gift", ("lunar", "qixi"), 20, 0, "七夕：东方浪漫、心意的语气",
       "QIXI FESTIVAL", "star_lights tongxin_knot magpie_paper rose love_letter red_string", "fest_qixi"),
    _f("school", "开学季", "season", ("range", (8, 20), (9, 10)), 10, 0, "开学季与教师节：新开始、元气、感谢的语气",
       "BACK TO SCHOOL", "notebook pencil books canvas_bag apple window_shadow", "fest_school"),
    _f("zhongqiu", "中秋", "traditional", ("lunar", "zhongqiu"), 30, 0, "中秋：团圆、诗意、送礼的语气",
       "MID-AUTUMN FESTIVAL", "mooncake moon_lamp osmanthus rabbit pomelo tea_cup", "fest_zhongqiu"),
    _f("guoqing", "国庆", "traditional", ("solar", 10, 1), 20, 6, "国庆：欢庆、出游、大气的语气（不写政治口号）",
       "GOLDEN WEEK", "lantern red_gold_silk xiangyun chinese_knot peony", "fest_guoqing"),
    _f("autumn", "秋冬暖意", "season", ("range", (10, 15), (11, 30)), 10, 0, "秋冬：温暖、舒适、换季呵护的语气",
       "WARM AUTUMN", "maple_leaf ginkgo pinecone hot_cocoa chestnut pumpkin knit", "fest_autumn"),
    _f("d1111", "双11", "promo", ("solar", 11, 11), 25, 0, "双11：全年最值、囤货、仪式感的语气",
       "DOUBLE 11", "red_velvet pearl crystal_holder rose_quartz glass_bubble gift_stack ribbon", "fest_1111"),
    _f("d1212", "双12", "promo", ("solar", 12, 12), 12, 0, "双12：年终回馈、好礼的语气",
       "DOUBLE 12", "gift_stack shopping_bag red_knit ribbon candle balloon", "fest_1212"),
    _f("dongzhi", "冬至", "traditional", ("lunar", "dongzhi"), 10, 0, "冬至：温暖、团圆、家常的语气",
       "WINTER SOLSTICE", "tangyuan dumplings hot_tea cream_wool red_knit", "fest_dongzhi"),
    _f("xmas", "圣诞", "gift", ("solar", 12, 25), 30, 1, "圣诞：温馨、惊喜、礼物的语气",
       "MERRY CHRISTMAS", "pipe_tree xmas_tree gingerbread bell stocking apple snow pinecone xmas_ball candy_cane wreath reindeer",
       "fest_xmas fest_xmas_snow"),
)
BY_KEY: dict[str, Festival] = {f.key: f for f in FESTIVALS}
BY_SCENE: dict[str, Festival] = {s: f for f in FESTIVALS for s in f.scenes}


def _lunar(key: str, year: int) -> date:
    table = _LUNAR[key]
    m, d = table.get(year) or table[max(table)]
    return date(year, m, d)


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def occurrence(f: Festival, year: int) -> tuple[date, date, date]:
    """某年的（开始备货, 节日当天, 结束）。区间类节点的「当天」= 区间开始。"""
    kind = f.rule[0]
    if kind == "range":
        (m1, d1), (m2, d2) = f.rule[1], f.rule[2]
        start = date(year, m1, d1)
        return start - timedelta(days=f.lead), start, date(year, m2, d2)
    if kind == "nianhuo":  # 年货节：12 月 26 日起到春节前一周
        end = _lunar("chunjie", year) - timedelta(days=7)
        start = date(year - 1, 12, 26)
        return start, start, end
    if kind == "solar":
        day = date(year, f.rule[1], f.rule[2])
    elif kind == "lunar":
        day = _lunar(f.rule[1], year)
    else:
        day = _nth_sunday(year, f.rule[1], f.rule[2])
    return day - timedelta(days=f.lead), day, day + timedelta(days=f.tail)


def next_occurrence(f: Festival, today: date | None = None) -> dict:
    """最近一次（正在进行或即将到来）：日期、是否进行中、距节日天数、是否临近。"""
    today = today or date.today()
    for year in (today.year, today.year + 1, today.year + 2):
        start, day, end = occurrence(f, year)
        if end >= today:
            break
    active = start <= today <= end
    return {"start": start.isoformat(), "date": day.isoformat(), "end": end.isoformat(), "active": active,
            "days": (day - today).days, "soon": active or (start - today).days <= SOON_DAYS,
            "_order": (0 if active else 1, day)}


def festival_out(f: Festival, today: date | None = None) -> dict:
    """序列化给前端。"""
    occ = next_occurrence(f, today)
    occ.pop("_order")
    return {"key": f.key, "name": f.name, "type": f.type, "type_name": FESTIVAL_TYPES[f.type], "en": f.en, **occ}


def sort_key(f: Festival, today: date | None = None) -> tuple:
    """排序：进行中在前，其余按节日当天的日期。"""
    return next_occurrence(f, today)["_order"]


def ordered(today: date | None = None) -> list[Festival]:
    return sorted(FESTIVALS, key=lambda f: sort_key(f, today))


def of_scene(scene_key: str | None) -> Festival | None:
    return BY_SCENE.get(scene_key or "")


def pack_keys(f: Festival) -> list[str]:
    return [f"el_{k}" for k in f.pack]


def copy_note(f: Festival) -> str:
    """给规划模型的节日文案约束。"""
    return (f"本套是「{f.name}」节日主题：{f.tone}；英文副标可参考「{f.en}」。"
            "不要编造折扣、价格、满减和活动规则，不写天猫、京东、小红书等平台名")
