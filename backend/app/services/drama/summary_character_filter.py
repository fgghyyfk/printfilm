"""Conservative category checks before summary entries become character assets."""

from __future__ import annotations

import re
from typing import Any


_NON_CHARACTER_ROLES = frozenset(
    {"场景", "场景角色", "环境", "环境角色", "氛围角色", "非人物", "非角色", "非拟人化道具", "道具"}
)
_NON_CHARACTER_DECLARATION_RE = re.compile(
    r"^(?:非人物|非角色|非拟人化(?:角色|道具|物体)|无生命(?:物体|道具))(?=[，,。:：;；\s]|$)"
)
_INANIMATE_IDENTITY_RE = re.compile(
    r"植物种子|生态系统|生态环境|自然景观|无生命|非生物|道具|器物|建筑|场景|环境|"
    r"(?:^|[，,。:：;；\s])(?:植物|种子)(?=[，,。:：;；\s]|$)"
)
_NEGATED_CHARACTER_IDENTITY_RE = re.compile(
    r"非人物|非角色|非拟人化(?:角色|道具|物体)?|不拟人化|"
    r"(?:无|没有|不具备|不具有)(?:人格|自主意识|意志)"
)
_CHARACTER_IDENTITY_RE = re.compile(
    r"拟人|人格|自主意识|对话|人类|人形|男(?:人|孩|子|性)|女(?:人|孩|子|性)|"
    r"树妖|树精|精灵|妖怪|机器人|群演|路人|村民|士兵|工作人员|道具师"
)


def _is_non_character_entry(character: dict[str, Any]) -> bool:
    """Require an explicit category declaration and non-character identity evidence.

    Animals and group characters remain valid. Ambiguous entries are retained;
    this check only prevents clearly labelled scenery/objects from being seeded
    with the character three-view and voice workflow.
    """
    role = str(character.get("roleType") or "").strip()
    role_parts = {part.strip() for part in re.split(r"[/／、,，+|]", role)}
    visual = str(character.get("visualImage") or "").strip()
    identity = str(character.get("identityBackground") or "").strip()
    declared_non_character = bool(
        role_parts & _NON_CHARACTER_ROLES
        or _NON_CHARACTER_DECLARATION_RE.match(visual)
        or _NON_CHARACTER_DECLARATION_RE.match(identity)
    )
    if not declared_non_character:
        return False

    # A mislabelled tree spirit, person or speaking robot still belongs to cast.
    identity_text = " ".join(
        str(character.get(key) or "")
        for key in ("name", "title", "visualImage", "identityBackground")
    )
    identity_text = _NEGATED_CHARACTER_IDENTITY_RE.sub("", identity_text)
    if _CHARACTER_IDENTITY_RE.search(identity_text):
        return False

    return bool(
        role_parts & {"非人物", "非角色", "非拟人化道具"}
        or re.match(r"^(?:非人物|非角色)", visual)
        or re.match(r"^(?:非人物|非角色)", identity)
        or _INANIMATE_IDENTITY_RE.search(identity)
        or re.match(r"^(?:无生命|非生物)", visual)
    )


def partition_summary_characters(
    summary: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Return usable biographies and explicitly excluded names without mutation.

    Keeping excluded names lets seed ignore the same erroneous entry if a model
    also repeated it in a screenplay cast line. A valid duplicate wins over an
    excluded duplicate so an ambiguous name cannot suppress a genuine character.
    """
    accepted: dict[str, dict[str, Any]] = {}
    excluded: set[str] = set()
    for character in summary.get("characters") or []:
        if not isinstance(character, dict):
            continue
        name = str(character.get("name") or "").strip()
        if not name:
            continue
        if _is_non_character_entry(character):
            excluded.add(name)
        else:
            accepted[name] = character
    return accepted, excluded - accepted.keys()
