"""Subtle, local prosody adjustments for TT's local speech."""

from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import subprocess
import wave


FFMPEG = Path(os.environ.get("TT_FFMPEG", "ffmpeg"))
DISTRESS = re.compile(r'好难过|很难过|不开心|好累|很累|累坏了|好烦|很烦|心里难受|想哭|睡不着|有点崩溃|好委屈|撑不住')
COMFORT = re.compile(r'^(?:嗯[，。 ]*|唔[，。 ]*)?(?:抱抱|别急|不着急|慢慢来|我在呢|先歇一会儿|辛苦了|听起来真的很)')
CELEBRATION = re.compile(r'^(?:太好了|真棒|恭喜|好耶|哇[，！])')
TEASING = re.compile(r'^(?:哼[，。 ]*|才不是呢|我可没[有说]|算你厉害|就这一次哦)')
WORK = re.compile(r'^(?:先说结论|可以分两步|具体来说|第一步|先打开|先检查|原因是|请先)')
BREATH = re.compile(r'^(嗯|唔|诶|哎|好呀|好啦|不过|其实|对了)(?=[\u3400-\u9fff])')
PACE = {
    'comfort': (0.92, -0.55),
    'cheer': (1.06, 0.70),
    'tease': (1.02, 0.30),
    'explain': (0.98, 0.0),
    'chat': (1.0, 0.0),
}


@dataclass(frozen=True)
class Delivery:
    text: str
    speed: float
    pitch: float


def plan(text, context='', rate=0):
    """Use local conversation cues and the user's existing rate setting."""
    if WORK.search(text):
        scene = 'explain'
    elif COMFORT.search(text) or DISTRESS.search(context):
        scene = 'comfort'
    elif CELEBRATION.search(text):
        scene = 'cheer'
    elif TEASING.search(text):
        scene = 'tease'
    else:
        scene = 'chat'
    speed, pitch = PACE[scene]
    text = re.sub(r'[！!]{2,}', '！', text)
    text = re.sub(r'[？?]{2,}', '？', text)
    text = re.sub(r'\s*\n\s*', '\n', text)
    text = re.sub(r'(?<![。！？.!?；;，,：:])\n', '。\n', text).replace('\n', ' ')
    text = BREATH.sub(r'\1，', text)
    return Delivery(text, max(0.45, min(1.6, (1 + rate / 200) * speed)), pitch)


def shift_pitch(data, semitones):
    """Shift pitch while preserving duration and vocal formants."""
    if not semitones or not FFMPEG.is_file():
        return data
    with wave.open(io.BytesIO(data), 'rb') as audio:
        if audio.getsampwidth() != 2:
            return data
        rate, channels = audio.getframerate(), audio.getnchannels()
        samples = audio.readframes(audio.getnframes())
    ratio = 2 ** (semitones / 12)
    try:
        result = subprocess.run(
            [str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-nostdin',
             '-f', 's16le', '-ar', str(rate), '-ac', str(channels), '-i', 'pipe:0',
             '-af', f'rubberband=pitch={ratio:.8f}:formant=preserved:pitchq=quality',
             '-f', 's16le', 'pipe:1'],
            input=samples, capture_output=True, timeout=12, check=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if getattr(exc, 'winerror', None) == 32:
            raise
        return data
    if not result.stdout or len(result.stdout) % (2 * channels):
        return data
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(result.stdout)
    return output.getvalue()
