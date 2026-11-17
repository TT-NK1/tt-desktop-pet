"""CPU-only local speech, subtitles and video frames. Runs outside the Qt process."""

import base64
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys


MAX_SECONDS = 180
MAX_TEXT = 3000


def ffmpeg(binary, source, *args):
    return subprocess.run([
        binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe',
        '-threads', '2', '-i', source, *args, 'pipe:1',
    ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=38,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout


def subtitles(binary, source):
    raw = ffmpeg(binary, source, '-t', str(MAX_SECONDS), '-map', '0:s:0', '-f', 'srt')
    lines = raw.decode('utf-8', errors='replace').splitlines()
    return '\n'.join(re.sub(r'<[^>]+>', '', line) for line in lines
                     if line.strip() and not line.strip().isdigit() and '-->' not in line)


def video_frames(binary, source):
    frames = []
    for second in (0, 15):
        frame = ffmpeg(binary, source, '-ss', str(second), '-map', '0:v:0', '-an', '-frames:v', '1',
                       '-vf', "scale='min(900,iw)':-2", '-q:v', '4', '-f', 'image2pipe', '-c:v', 'mjpeg')
        if frame.startswith(b'\xff\xd8') and frame.endswith(b'\xff\xd9') and len(frame) <= 700_000:
            frames.append(base64.b64encode(frame).decode('ascii'))
    return frames


def transcribe(audio, runtime):
    model_path = runtime / 'whisper-base'
    if not all((model_path / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json')):
        raise ValueError('本机语音识别模型未下载完成。')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    sys.path.insert(0, str(runtime / 'python'))
    from faster_whisper import WhisperModel

    model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=3, local_files_only=True)
    segments, info = model.transcribe(io.BytesIO(audio), task='transcribe', beam_size=3,
                                      multilingual=True, language_detection_segments=3,
                                      vad_filter=True, condition_on_previous_text=False)
    text = ' '.join(segment.text.strip() for segment in segments if segment.text.strip())
    return text[:MAX_TEXT], info.language if text else ''


def analyse(source, kind, runtime, binary):
    if kind not in ('audio', 'video'):
        raise ValueError('请选择本地音频或视频附件。')
    if not Path(source).is_file():
        raise ValueError('本地音视频文件已找不到。')
    frames = video_frames(binary, source) if kind == 'video' else []
    captions = subtitles(binary, source) if kind == 'video' else ''
    if captions.strip():
        return dict(kind='字幕', text=captions[:MAX_TEXT], language='', frames=frames)
    audio = ffmpeg(binary, source, '-t', str(MAX_SECONDS), '-map', '0:a:0', '-ac', '1', '-ar', '16000',
                   '-c:a', 'pcm_s16le', '-f', 'wav')
    text, language = transcribe(audio, runtime) if len(audio) > 6000 else ('', '')
    if not audio and not frames:
        raise ValueError('无法读取该文件的音轨或画面。')
    return dict(kind='语音转写', text=text, language=language, frames=frames)


if __name__ == '__main__':
    runtime, binary, kind, source = sys.argv[1:5]
    try:
        result = analyse(source, kind, Path(runtime), binary)
    except ValueError as error:
        result = {'error': str(error)}
    except (OSError, RuntimeError, ImportError, subprocess.TimeoutExpired):
        result = {'error': '本机未能完成识别，请确认文件可播放后重试。'}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
