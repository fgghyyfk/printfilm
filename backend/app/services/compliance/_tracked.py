"""内部码点来源跟踪：记录实际替换的完整词，清理也能精确还原。"""
from difflib import SequenceMatcher


class TrackedText:
    """每个输出码点保留原稿来源区间，避免差异对齐拆散完整替换词。"""

    def __init__(self, text: str):
        """以原稿的每个Unicode码点初始化来源。"""
        self.original = text
        self.chars = [(char, i, i + 1) for i, char in enumerate(text)]
        self.touched: list[tuple[int, int]] = []

    @property
    def text(self) -> str:
        """返回当前文本。"""
        return "".join(char for char, _, _ in self.chars)

    def replace(self, start: int, end: int, replacement: str, *, record: bool = True) -> None:
        """替换当前区间，并将新码点绑定到被替换的原稿词区间。"""
        selected = self.chars[start:end]
        if not selected:
            # 清理步骤一般只删除；插入时绑定相邻码点，保证仍有可逆的原稿跨度。
            selected = self.chars[max(0, start - 1):start] or self.chars[start:start + 1]
        lo = min((a for _, a, _ in selected), default=0)
        hi = max((b for _, _, b in selected), default=0)
        if record:
            self.touched.append((lo, hi))
        self.chars[start:end] = [(char, lo, hi) for char in replacement]

    def literal(self, word: str, replacement: str, *, record: bool = True) -> str:
        """逐处替换字面词，重复命中保持独立区间。"""
        text = self.text
        positions: list[int] = []
        pos = text.find(word)
        while pos >= 0:
            positions.append(pos)
            pos = text.find(word, pos + len(word))
        for pos in reversed(positions):
            self.replace(pos, pos + len(word), replacement, record=record)
        return self.text

    def sub(self, pattern, replacement: str) -> str:
        """逐处执行正则替换。"""
        for match in reversed(list(pattern.finditer(self.text))):
            self.replace(match.start(), match.end(), match.expand(replacement))
        return self.text

    def rewrite(self, result: str) -> None:
        """将末尾空白/标点清理记入同一份来源区间。"""
        opcodes = SequenceMatcher(None, self.text, result, autojunk=False).get_opcodes()
        for tag, a, b, c, d in reversed(opcodes):
            if tag != "equal":
                self.replace(a, b, result[c:d])

    def changes(self) -> list[dict]:
        """合并重叠操作，输出完整原词与最终新词的码点区间。"""
        merged: list[list[int]] = []
        for start, end in sorted(self.touched):
            if merged and start < merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
        result = self.text
        rows: list[dict] = []
        for start, end in merged:
            new_start = sum(b <= start for _, _, b in self.chars)
            new_end = new_start + sum(a < end and b > start for _, a, b in self.chars)
            old, new = self.original[start:end], result[new_start:new_end]
            if old != new:
                rows.append({"id": str(len(rows)), "old": old, "new": new,
                             "original_start": start, "original_end": end,
                             "start": new_start, "end": new_end, "undone": False})
        return rows
