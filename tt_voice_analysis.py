"""Offline PCM inspection. No uploads, conversion, or changes to source files."""
import math
from pathlib import Path
import wave


class AudioError(ValueError):
    pass


def analyze(path):
    """Bound work and memory; report energy activity, not semantic speech detection."""
    path = Path(path)
    try:
        with wave.open(str(path), 'rb') as stream:
            channels, width, rate, frames = (stream.getnchannels(), stream.getsampwidth(),
                                             stream.getframerate(), stream.getnframes())
            if width not in (1, 2, 3, 4) or not 1 <= channels <= 8 or rate <= 0:
                raise AudioError('不支持此 PCM 参数；请另存为 16 位 PCM WAV，保留原件。')
            if frames == 0 or frames / rate > 300 or path.stat().st_size > 128 * 1024 * 1024:
                raise AudioError('请选择非空且不超过 5 分钟、128 MB 的样本。')
            peak, squares, count, clipped = 0.0, 0.0, 0, 0
            levels = []
            block = max(1, rate // 50)
            read_frames = 0
            while True:
                raw = stream.readframes(block)
                if not raw:
                    break
                if len(raw) % (width * channels):
                    raise AudioError('音频数据不完整，请重新导出。')
                values = []
                for offset in range(0, len(raw), width):
                    chunk = raw[offset:offset + width]
                    value = ((chunk[0] - 128) / 128 if width == 1 else
                             int.from_bytes(chunk, 'little', signed=True) / (2 ** (8 * width - 1)))
                    values.append(value)
                energy = sum(v * v for v in values)
                levels.append((len(values) / channels / rate, math.sqrt(energy / len(values))))
                squares += energy
                count += len(values)
                read_frames += len(values) // channels
                peak = max(peak, max(abs(v) for v in values))
                clipped += sum(abs(v) >= .999 for v in values)
            if read_frames != frames or not count:
                raise AudioError('音频数据截断或为空，请重新导出。')
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioError('无法解码：当前仅支持未压缩 PCM WAV；MP3/M4A/FLAC 需要额外解码器。') from exc
    rms = math.sqrt(squares / count)
    threshold = max(.01, rms * .25)
    active = sum(duration for duration, level in levels if level > threshold)
    warnings = []
    if rms < .01:
        warnings.append('音量过低：靠近麦克风并适度提高录音增益。')
    if clipped / count > .001:
        warnings.append('疑似削波：降低录音增益，避免爆音。')
    warnings.append('无法可靠检测背景噪声、多人混说或音高；请试听确认单人、安静、无音乐。')
    return dict(duration=frames / rate, sample_rate=rate, channels=channels,
                sample_width=width, peak=peak, rms=rms,
                energy_active_seconds=active, quiet_seconds=frames / rate - active,
                warnings=warnings,
                limitations='能量阈值仅估计响声和安静时间，不是模型 VAD。未进行转写、语速或音高分析；不测量主观音色。')
