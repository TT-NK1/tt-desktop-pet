"""Extract speakable phrases from an incremental TT text reply."""

import re

from tt_natural_speech import happy_intro_end, short_followup_extent
from tt_speech_segments import ChineseTextSegmenter


MARKUP = re.compile(
    r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)|"
    r"`[^`\n]*(?:`|\Z)|\[[^\]\n]*(?:\](?:\([^\n)]*(?:\)|\Z))?|\Z)",
    re.DOTALL,
)


class LiveReply:
    def __init__(self, turn, settings):
        self.turn = turn
        self.settings = settings
        self.tail = ''
        self.consumed = ''
        self.stopped = False
        self.delivery = None
        self.segmenter = ChineseTextSegmenter()

    def feed(self, text):
        if self.stopped:
            return
        self.tail += text
        start = count = join_until = 0
        intro_end = happy_intro_end(self.tail) if self.settings.get('speech_style', 'auto') in ('auto', 'happy') else 0
        masked = MARKUP.sub(lambda match: " " * len(match[0]), self.tail)
        for end, mark in self.segmenter.boundaries(masked, final=False):
            if end < join_until:
                continue
            if not mark.startswith("\n"):
                count += 1
            # Keep short reactions together; start a complete passage while a long reply is still arriving.
            if end <= intro_end or (count < 3 and end - start < 48):
                continue
            following = self.tail[end:]
            stripped = following.lstrip()
            if not stripped:
                break
            extent = short_followup_extent(stripped)
            if extent == 0:
                break
            if extent is not None:
                join_until = end + len(following) - len(stripped) + extent
                continue
            phrase = self.tail[start:end].strip(" \t")
            start, count = end, 0
            if phrase.strip():
                yield phrase
        self.consumed += self.tail[:start]
        self.tail = self.tail[start:]

    def finish(self, answer):
        if self.stopped:
            return ''
        rest = answer[len(self.consumed):] if answer.startswith(self.consumed) else self.tail
        self.stop()
        return rest.strip()

    def stop(self):
        self.stopped = True
        self.tail = ''
