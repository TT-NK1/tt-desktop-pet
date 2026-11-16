"""Boundaries and pause budgets shared by the Chinese speech queue and worker."""
import re

NATIVE_TOKEN_LIMIT = 120  # IndexTTS2.infer() default, in BPE tokens.
ENTITIES = re.compile(
    r"(?:https?://|www\.)[A-Za-z0-9%._~:/?#\[\]@!$&'()*+,;=\-]+"
    r"|(?<=[\"“])[A-Za-z]:[\\/][^\"”\n]+"
    r"|[A-Za-z]:[\\/][^\s，。！？；：、”’」』）】]+"
    r"|(?:\.{1,2}/|/)[A-Za-z0-9_./\-]+"
    r"|(?:(?:[A-Za-z]\.){2,}|(?:Mr|Mrs|Ms|Dr|Prof|No|vs|etc)\.)(?=\s*[\w\u3400-\u9fff])"
    r"|[A-Za-z0-9_]+(?:[.'\-+:/@][A-Za-z0-9_]+)+"
)
MARKS = re.compile(r"(?P<entity>" + ENTITIES.pattern + r")|[。！？!?；;]+|\.{3,}|…+|\.(?=\s|$)|\n+|——|[，、,:：]|[「」『』“”‘’（）()【】［］《》\"\[\]]")
OPEN = dict(zip('「『“‘（(【［《[', '」』”’）)】］》]'))
TERMINAL = "。！？!?；;."
WEAK = "，、,:："


class ChineseTextSegmenter:
    @staticmethod
    def normalize(text, scene="chat"):
        text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip().rstrip(" \t")
        if scene == "weather":
            # A completed finding followed by advice. Keep conditional clauses together.
            text = re.sub(r"(?<=[哦呀啦呢])[，,](?=\s*所以\s*如果)", "。", text)
        return text

    def boundaries(self, text, *, weak=False, final=True):
        stack = []
        quote_stop = False
        for m in MARKS.finditer(text):
            value = m[0]
            if m.lastgroup == "entity":
                # Sentence punctuation at the end of a URL/path belongs to the surrounding prose.
                suffix = value.rstrip(".!?;,")
                if not stack and suffix != value and ("/" in value or "\\" in value) and (final or m.end() < len(text)):
                    if value[-1] in TERMINAL or weak:
                        yield m.end(), value[-1]
                quote_stop = False
                continue
            if stack and value == stack[-1]:
                stack.pop()
                if not stack and quote_stop and not text[m.end():].startswith(
                        ("，", "。", "？", "！", "；", "、", "然后", "说", "问")):
                    yield m.end(), "。"
                continue
            if value in OPEN or value == "\"":
                stack.append(OPEN.get(value, "\""))
                quote_stop = False
                continue
            if stack:
                quote_stop = value[0] in TERMINAL and not text[m.end():m.end()+1].isalnum()
                continue
            if value.startswith("\n") or value[0] in TERMINAL and not value.startswith(("...", "…")):
                if final or value != "." or m.end() < len(text):
                    yield m.end(), value
            elif weak and (value[0] in WEAK + "…" or value in {"——", "..."}):
                yield m.end(), value

    def queue_blocks(self, text, *, responsive=False):
        """Batch long replies at complete sentences with a shorter first chat clip."""
        start = count = 0
        edges = list(self.boundaries(text))
        for index, (end, mark) in enumerate(edges):
            if not mark.startswith("\n"):
                count += 1
            chat_limit = 36 if start == 0 else 80
            long_batch = count >= 10 and index + 2 < len(edges)
            chat_batch = responsive and end - start >= chat_limit and len(text[end:].strip()) >= 18
            if long_batch or chat_batch:
                if text[start:end].strip():
                    yield text[start:end].strip(" \t")
                start, count = end, 0
        if text[start:].strip():
            yield text[start:].strip(" \t")

    def fit_tokens(self, text, token_count=None, limit=NATIVE_TOKEN_LIMIT):
        start = 0
        for end, mark in self.boundaries(text):
            if mark.startswith("\n"):
                yield from self._fit_paragraph(text[start:end], token_count, limit)
                start = end
        yield from self._fit_paragraph(text[start:], token_count, limit)

    def _fit_paragraph(self, text, token_count, limit):
        if not text.strip():
            return
        if token_count is None:
            yield text
            return
        edges = list(self.boundaries(text, weak=True))
        start = 0
        while token_count(text[start:]) > limit:
            candidates = []
            for end, mark in edges:
                if end <= start:
                    continue
                size = token_count(text[start:end])
                if size > limit:
                    break
                candidates.append((end, mark, size))
            if not candidates:
                break  # Let the native tokenizer handle an indivisible run.
            strong = [end for end, mark, size in candidates if mark[0] in TERMINAL + "\n"
                      and size >= limit // 3]
            end = strong[-1] if strong else candidates[-1][0]
            yield text[start:end]
            start = end
        if text[start:].strip():
            yield text[start:]


class PauseController:
    @staticmethod
    def for_text(text):
        tail = text.rstrip(" \t”’」』）)]\"")
        if tail.endswith("\n"):
            return 480
        if tail.endswith(("…", "...")):
            return 330
        if tail.endswith(("？", "?", "！", "!")):
            return 320
        if tail.endswith(("；", ";")):
            return 235
        if tail.endswith(tuple(WEAK) + ("——",)):
            return 155
        return 300

    @staticmethod
    def additional(target, leading, trailing, elapsed=0):
        return max(0, round(target - leading - trailing - elapsed))
