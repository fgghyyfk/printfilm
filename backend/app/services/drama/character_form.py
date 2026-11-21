"""判断角色本体是否为非人形（会说话的纸鹤、动物、器物等），用于选择角色设定板模板。

人物设定板模板要求五官、发型、服饰，会把「一只纸鹤」画成手托纸鹤的人。
这里只看描述开头一句，保守判定：开头是「一只/一封/一台…」等量词或「…折成/制成的」，
且这句里没有人物称谓时，才算非人；描述里明确要求人形（化为人形、拟人化人形）则仍按人物处理。
"""

from __future__ import annotations

import re

# 描述开头的非人量词（「一位/一个」不算，人物也常用）
_NON_HUMAN_LEAD_RE = re.compile(
    r"^(?:外观[：:]\s*)?(?:是)?一(?:只|头|匹|条|尾|封|台|把|盏|棵|株|朵|枚|颗|架|辆|艘|本|张|片|团|缕|座|块|面|支|根|顶|柄)"
)
# 「由…折成/制成的」这类器物来历
_CRAFTED_RE = re.compile(r"(?:折|制|做|雕|编|捏|缝|铸|刻|拼)成的")
# 开头一句里出现即视为人物
_HUMAN_MARKER_RE = re.compile(
    r"人|少女|少年|男|女|孩|岁|爷|奶|婆|叔|姨|姐|妹|哥|弟|先生|小姐|青年|老者|士兵|武士|侠|僧|道士|王子|公主|仙子"
)
# 描述里明确要求人形
_HUMANOID_REQUEST_RE = re.compile(r"化为人形|化成人形|人形形态|拟人化人形|人类外貌|人类模样")
# 开头一句的结束符
_FIRST_CLAUSE_RE = re.compile(r"[，,。；;！!？?\n]")


def is_non_human_character(description: str | None) -> bool:
    """角色描述（不含结构前缀的正文）是否为非人形本体。"""
    text = (description or "").strip()
    if not text or _HUMANOID_REQUEST_RE.search(text):
        return False
    first = _FIRST_CLAUSE_RE.split(text, maxsplit=1)[0]
    if _HUMAN_MARKER_RE.search(first):
        return False
    return bool(_NON_HUMAN_LEAD_RE.match(first) or _CRAFTED_RE.search(first))
