"""Conservative delivery presets for the single, existing TT speaker."""
import re
from dataclasses import dataclass
from tt_speech_segments import ChineseTextSegmenter, PauseController, NATIVE_TOKEN_LIMIT

EMOTION_ORDER = ("happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm")


@dataclass(frozen=True)
class Style:
    label: str
    duration: float
    emotion: tuple[float, ...]


STYLES = {
    "chat": Style("日常", 1.00, (0, 0, 0, 0, 0, 0, 0, 0)),
    "happy": Style("开心", .95, (.40, 0, 0, 0, 0, 0, .06, 0)),
    "excited": Style("兴奋", .92, (.42, 0, 0, 0, 0, 0, .14, 0)),
    "tsundere": Style("傲娇", .98, (.10, .12, 0, 0, 0, 0, 0, .08)),
    "shy": Style("害羞", 1.06, (.09, 0, 0, .16, 0, .04, 0, .13)),
    "hesitant": Style("犹豫", 1.08, (0, 0, 0, .14, 0, .08, 0, .16)),
    "angry": Style("生气", .97, (0, .38, 0, 0, 0, 0, 0, .05)),
    "hurt": Style("委屈", 1.07, (0, 0, .26, 0, 0, .16, 0, .08)),
    "sad": Style("悲伤", 1.09, (0, 0, .33, 0, 0, .15, 0, 0)),
    "surprise": Style("惊讶", .96, (.09, 0, 0, 0, 0, 0, .40, 0)),
    "calm": Style("平静", 1.04, (0, 0, 0, 0, 0, 0, 0, .40)),
    "affectionate": Style("撒娇", 1.03, (.17, 0, 0, 0, 0, .07, 0, .24)),
}
STRENGTH = 65
BRIGHT_STYLES = frozenset(("happy", "excited", "surprise"))
HAPPY_OPENING = Style("开心开头", .95, (.40, 0, 0, 0, 0, 0, .11, .02))
HAPPY_RELEASE = Style("开心收尾", 1.045, (.18, 0, 0, 0, 0, 0, .02, .22))
HAPPY_PAUSE_MS = 125
LINKED_FOLLOWUP_PAUSE_MS = 300
_HAPPY_INTRO = re.compile(
    r"\A(?:[^。！？!?…\n]{1,14}[？?]\s*)?"
    r"(?:[啊哇嗯诶][，,]\s*)?(?:太好了|太棒了|好耶|真棒|真开心|太开心了)"
    r"[，,。！!]+[”’」』）)]*"
)

# Use cues in the spoken reply. A sad user message alone should not make TT sad.
CUES = [
    ("shy", r"才[、，,]才|才不是特意|你不要误会|不好意思说"),
    ("affectionate", r"(?:再)?陪我.{0,8}(?:嘛|好不好|行不行)|拜托嘛|再待一会儿嘛"),
    ("hurt", r"没有认真听|没有听我说|有点委屈|你都没听|说好了.{0,7}却"),
    ("sad", r"我.{0,7}(?:难过|想哭)|(?:真的|还是)有点难过|再也见不到"),
    ("angry", r"我有点生气|(?:这|你)太过分了|不该这样说|这样真的不行"),
    ("excited", r"太棒了|我们做到了|这也太.{1,5}了吧"),
    ("happy", r"太好了|真开心|恭喜|真棒|好耶"),
    ("surprise", r"^(?:嗯[？?]|诶[？?]|啊[？?]|什么[？?]|真的吗[？?])|没想到.{0,8}竟然"),
    ("hesitant", r"^(?:嗯[，,。…]|这个[，,…]|我[、，,]我)|让我想想|我不太确定"),
    ("calm", r"慢慢来|先别着急|没关系|我在呢|不着急"),
]

_FOLLOWUP_LISTENERS = ("你们", "你", "您")
_FOLLOWUP_LEAD = r"(?:那)?(?:你们|你|您)呢[，,][ \t]*"
_FOLLOWUP_BODY = r"[^，,、：:。！？!?；;…\n]"
_SHORT_FOLLOWUP = re.compile(
    r"(?P<boundary>^|[。！？!?\n]\s*)" + _FOLLOWUP_LEAD
    + _FOLLOWUP_BODY + r"{3,24}(?=[？?])"
)
_DRAFT_FOLLOWUP = re.compile(_FOLLOWUP_LEAD + _FOLLOWUP_BODY + r"{0,24}")
_FOLLOWUP_PREFIXES = tuple(lead + listener + "呢" + comma
                           for lead in ("", "那") for listener in _FOLLOWUP_LISTENERS
                           for comma in ("，", ","))


def short_followup_extent(text):
    """Return the end of a complete follow-up, zero for a draft, or None."""
    match = _SHORT_FOLLOWUP.match(text)
    if match:
        return match.end() + 1
    if _DRAFT_FOLLOWUP.fullmatch(text) or any(prefix.startswith(text) for prefix in _FOLLOWUP_PREFIXES):
        return 0
    return None


def style_for(text, selected="auto"):
    if selected in STYLES:
        return selected
    return next((style for style, pattern in CUES if re.search(pattern, text)), "chat")


def controls(style, rate=0, strength=STRENGTH):
    return _preset_options(STYLES[style], rate, strength)


def _preset_options(preset, rate, strength):
    speed = 1.0 + max(-100, min(100, int(rate))) / 200
    factor = round(max(.5, min(2.0, preset.duration / speed)), 3)
    alpha = max(0, min(100, int(strength))) / 100
    return {"duration_factor": factor,
            "emo_vector": list(preset.emotion) if alpha and any(preset.emotion) else None,
            "emo_alpha": alpha,
            "use_random": False}


def happy_intro_end(text):
    match = _HAPPY_INTRO.match(text)
    return match.end() if match else 0


def automatic_reaction(text, style, strength):
    """Keep bright delivery on a local reaction instead of carrying it into narration."""
    if style not in BRIGHT_STYLES:
        return style, strength
    first_end = next((end for end, _ in ChineseTextSegmenter().boundaries(text)), len(text))
    if happy_intro_end(text) or style_for(text[:min(first_end, 36)]) in BRIGHT_STYLES:
        return style, round(strength * .6)
    return "chat", strength


def _spoken_chunk(text, options):
    lead = _SHORT_FOLLOWUP.match(text)
    if lead and lead.end() == len(text.rstrip("？?")):
        options = dict(options, compact_followup=True)
    return text, options


def _delivery_chunks(text, token_count=None, limit=NATIVE_TOKEN_LIMIT):
    segmenter = ChineseTextSegmenter()
    start = 0
    for match in _SHORT_FOLLOWUP.finditer(text):
        boundary = match.end("boundary")
        if boundary > start:
            yield from segmenter.fit_tokens(text[start:boundary], token_count, limit)
            yield from segmenter.fit_tokens(text[boundary:match.end() + 1], token_count, limit)
            start = match.end() + 1
    yield from segmenter.fit_tokens(text[start:], token_count, limit)


def delivery_plan(text, style, rate=0, strength=STRENGTH, *, token_count=None, token_limit=NATIVE_TOKEN_LIMIT):
    """Use the tested F contour only when there is a clear reaction and a continuation."""
    text = text.strip()
    intro_end = happy_intro_end(text) if style == "happy" else 0
    if intro_end and len(text[intro_end:].strip()) >= 6:
        intro, continuation = text[:intro_end], text[intro_end:]
        tails = list(_delivery_chunks(continuation, token_count, token_limit))
        softer = round(max(0, min(100, int(strength))) * 55 / 65)
        parts = [(intro, _preset_options(HAPPY_OPENING, rate, strength))]
        parts.extend(_spoken_chunk(tail, _preset_options(HAPPY_RELEASE, rate, softer)) for tail in tails)
        pauses = [HAPPY_PAUSE_MS, *(pause_after(tail) for tail in tails[:-1])]
    else:
        chunks = list(_delivery_chunks(text, token_count, token_limit))
        parts = [_spoken_chunk(chunk, controls(style, rate, strength)) for chunk in chunks]
        pauses = [pause_after(chunk) for chunk in chunks[:-1]]
    for index, (_, options) in enumerate(parts[1:]):
        if options.get("compact_followup"):
            pauses[index] = LINKED_FOLLOWUP_PAUSE_MS
    return parts, pauses


def pause_after(text):
    return PauseController.for_text(text)
