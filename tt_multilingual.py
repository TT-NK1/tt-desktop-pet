"""Language policy and paths for TT's local multilingual tools."""

from functools import lru_cache
from pathlib import Path
import os
import re
import shutil
import sys
from itertools import chain


RUNTIME = Path(__file__).resolve().parent / 'runtime' / 'multilingual'
QWEN_RUNTIME = RUNTIME / 'qwen'
ASR_PYTHON = Path(os.environ.get('TT_ASR_PYTHON') or sys.executable)
FFMPEG = Path(os.environ.get('TT_FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg')
AUDIO_SUFFIXES = {'.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus', '.aac', '.wma'}
VIDEO_SUFFIXES = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.wmv', '.mpeg', '.mpg'}
MEDIA_SUFFIXES = AUDIO_SUFFIXES | VIDEO_SUFFIXES
READ_PREFIX = re.compile(
    r'(?:请|帮我|麻烦你)?(?:朗读|读)(?:一下|一遍|出来)?(?:这句话|这段文字|这句|这段)?[：:]\s*(.+)', re.S,
)
READ_SUFFIX = re.compile(
    r'(.+?)(?:[，,；;]\s*|\s+|(?<=[。！？.!?」』”》]))'
    r'(?:帮我|请你?|麻烦你)?(?:朗读|读)(?:出来|一遍|一下|给我听)?[吧呗呀哦。！!？?]*', re.S,
)
CHINESE_GLOSS = re.compile(r'(意思是|含义是)[ \t]*[「『“]([^」』”\n]+)[」』”][ \t]*(?:…+|\.{3,})(?=[ \t]*[^\W_])')

LANGUAGE_RULE = (
    '识别用户本轮用的语言，默认用同一种语言自然回复。用户明确要求的输出语言优先。'
    '句子中夹带外文、歌词、代码、译文或附件时，按用户问题的主要语言回答，保留需引用的原文。'
    '只有图片或媒体且用户没有明确指定语言时，沿用最近对话语言，其他情况默认中文。'
    '能读清图片文字时可以按原文转录或翻译，并区分视频画面、现有字幕和本机语音转写。'
    '对歌曲或视频只描述实际收到的画面、字幕、转写或公开资料。不要根据标题编造歌词或台词。'
)


RECOGNITION_READ_RULE = (
    '图片文字、字幕或音视频转写只保留一份能确认的原文。中文、日语、韩语或英文直接输出原文，'
    '保留原文语言和自然标点，无需翻译、逐句复述或添加朗读标题。'
    '仅需说明没有文字的画面或内容时，用一段简短中文。'
)


def recognition_read_text(text):
    marker = re.search(r'(?m)^\s*(?:[#>*-]+\s*)?\*{0,2}TT\s*朗读\*{0,2}[：:]\*{0,2}\s*', text)
    return text[marker.end():].strip() if marker else text.strip()


def direct_read_text(prompt):
    if '【文本附件 · ' in prompt:
        return ''
    prompt = prompt.strip()
    match = READ_PREFIX.fullmatch(prompt) or READ_SUFFIX.fullmatch(prompt)
    return match[1].strip() if match else ''


def speech_sections(text, segmenter):
    """Keep adjacent sentences in the same language together for natural prosody."""
    start = section_start = 0
    language = None
    for end, _ in chain(segmenter.boundaries(text), ((len(text), ''),)):
        span = text[start:end]
        if not any(char.isalpha() for char in span):
            start = end
            continue
        detected = speech_language(span)
        if language and detected != language:
            yield language, text[section_start:start]
            section_start = start
        language = detected
        start = end
    if language:
        yield language, text[section_start:]
    elif any(char.isdigit() for char in text):
        yield 'zh', text


def chinese_speech_text(text):
    # Give the intro and quoted translation explicit pauses in IndexTTS.
    return CHINESE_GLOSS.sub(r'\1：\2。', text)


@lru_cache(maxsize=256)
def speech_language(text):
    # Keep proper names inside a sentence from switching voices on their own.
    plain = re.sub(r'https?://\S+|[\d_]+', ' ', text[:1600])
    if re.search(r'[\u3040-\u30ff\u31f0-\u31ff]', plain):
        return 'ja'
    if re.search(r'[\uac00-\ud7af\u1100-\u11ff]', plain):
        return 'ko'
    han = len(re.findall(r'[\u3400-\u9fff]', plain))
    letters = sum(ch.isalpha() for ch in plain)
    if han and han >= letters / 3:
        return 'zh'
    if not letters:
        return 'zh'
    language_path = str(RUNTIME / 'language')
    if language_path not in sys.path and Path(language_path).is_dir():
        sys.path.insert(0, language_path)
    try:
        from langdetect import DetectorFactory, LangDetectException, detect
    except ImportError:
        return 'en' if re.search(r'[A-Za-z]', plain) else 'zh'
    DetectorFactory.seed = 0
    try:
        return detect(plain).split('-')[0]
    except LangDetectException:
        return 'en'
