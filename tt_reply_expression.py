"""Separate TT's one per-reply expression from streamed conversation text."""

import re


PREFIX = '[[TT_EMOTION:'
LABELS = ('neutral', 'smile', 'happy', 'comfort', 'sad', 'puzzled', 'surprised', 'shy', 'focused', 'annoyed', 'sleepy')
HEADER = re.compile(r'^\[\[TT_EMOTION:([a-z_]{1,24})\]\][ \t]*')
INSTRUCTION = (
    'TT 的文字回复需要一个界面表情。根据用户本轮的上下文和措辞判断可能的语气或心情，'
    '对不确定、引用、假设或反讽保持克制。选择适合 TT 自己回应的表情。'
    '例如遇到好消息可以开心，用户委屈或焦虑时可以温和安慰，自己没理解可疑惑，'
    '不要模仿用户的愤怒，也不要声称能确定用户的真实心情。'
    '正文前单独一行发一个界面标签，严格使用 [[TT_EMOTION:标签]]，标签从 '
    + ', '.join(LABELS) + ' 中选择。接着正常回答，不要在正文引用该标签，也不要加表情动作旁白。'
)


def separate_header(text):
    candidate = text.lstrip()
    match = HEADER.match(candidate)
    if not match:
        return None, text
    label = match[1] if match[1] in LABELS else None
    return label, candidate[match.end():].lstrip('\r\n')


class ReplyExpressionStream:
    def __init__(self):
        self.header = ''
        self.decided = False
        self.skip_newline = False

    def feed(self, delta):
        if self.decided:
            if self.skip_newline:
                delta = delta.lstrip('\r\n')
                self.skip_newline = not delta
            return None, delta
        self.header += delta
        candidate = self.header.lstrip()
        if len(self.header) < 80 and (PREFIX.startswith(candidate)
                                      or candidate.startswith(PREFIX) and ']]' not in candidate):
            return None, ''
        self.decided = True
        label, text = separate_header(self.header)
        self.skip_newline = not text and HEADER.match(candidate) is not None
        self.header = ''
        return label, text

    def finish(self):
        tail, self.header = self.header, ''
        candidate = tail.lstrip()
        if candidate.startswith(PREFIX):
            return ''
        return tail
