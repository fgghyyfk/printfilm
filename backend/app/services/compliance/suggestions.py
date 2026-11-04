"""用户明确应用一条建议：短锁内核对原稿哈希与真实命中，保留其它文字。"""
import hashlib
from typing import Any

from app.services.compliance.content import scan_field, script_bodies

STALE_REPORT = '文案已变化，请重新检查后再替换'


def _spoken_positions(script: str) -> tuple[str, list[int]]:
    spoken, positions = [], []
    for body, offset in script_bodies(script):
        start = len(body) - len(body.lstrip())
        text = body.strip()
        spoken.append(text)
        positions.extend(range(offset + start, offset + start + len(text)))
    return ''.join(spoken), positions


def prepare_suggestion(owner: Any, data: dict, *, allowed: set[str]) -> tuple[dict, bool]:
    request = data.pop('compliance_apply', None)
    if not request:
        return data, False
    field = request['field']
    if data or field not in allowed:
        raise ValueError(STALE_REPORT)
    current = getattr(owner, field, '') or ''
    if hashlib.sha256(current.encode()).hexdigest() != request['text_hash']:
        raise ValueError(STALE_REPORT)
    hit = next((hit for hit in scan_field(current, field)
                if hit.start == request['start'] and hit.end == request['end']
                and hit.word == request['word'] and hit.suggestion is not None
                and hit.suggestion == request['suggestion']), None)
    if hit is None:
        raise ValueError(STALE_REPORT)
    result = {field: current[:hit.start] + hit.suggestion + current[hit.end:]}
    if field not in {'narration', 'segment_script', 'video_prompt'}:
        return result, True
    # 同步真实口播位置，不全文 find，也不把多段口播合并到第一段。
    if field == 'narration':
        narration = current
        start, end = hit.start, hit.end
    else:
        narration, positions = _spoken_positions(current)
        if hit.start not in positions:
            raise ValueError(STALE_REPORT)
        start = positions.index(hit.start)
        end = start + len(hit.word)
        if positions[start:end] != list(range(hit.start, hit.end)):
            raise ValueError(STALE_REPORT)
        if (getattr(owner, 'narration', '') or '') != narration:
            raise ValueError('关联口播已变化，请手动核对后修改')
        result['narration'] = narration[:start] + hit.suggestion + narration[end:]
    for related in ('segment_script', 'video_prompt'):
        if related == field:
            continue
        script = getattr(owner, related, '') or ''
        if not script:
            continue
        spoken, positions = _spoken_positions(script)
        if spoken != narration or end > len(positions):
            raise ValueError('关联口播已变化，请手动核对后修改')
        mapped_start, mapped_end = positions[start], positions[end - 1] + 1
        if script[mapped_start:mapped_end] != hit.word:
            raise ValueError(STALE_REPORT)
        result[related] = script[:mapped_start] + hit.suggestion + script[mapped_end:]
    return result, True
