"""Measure and cap silent PCM edges without touching voiced samples."""
from array import array
import io
import wave


def quiet_edge_frames(samples, rate, *, threshold, limit=None):
    frame = max(1, rate // 100)
    cutoff = min(len(samples) // 2 if limit is None else limit, rate // 2)
    for start in range(0, cutoff - frame + 1, frame):
        if max(map(abs, samples[start:start + frame])) >= threshold:
            return start
    return cutoff // frame * frame


def wav_edges(data, *, trim=False):
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            params = reader.getparams()
            if params.sampwidth != 2 or params.nchannels != 1:
                return data, 0, 0
            rate = params.framerate
            count = min(params.nframes // 2, rate // 2)
            first_samples, last_samples = array("h"), array("h")
            first_samples.frombytes(reader.readframes(count))
            reader.setpos(params.nframes-count)
            last_samples.frombytes(reader.readframes(count))
            last_samples.reverse()
            left = quiet_edge_frames(first_samples, rate, threshold=196, limit=count)
            right = quiet_edge_frames(last_samples, rate, threshold=196, limit=count)
            if trim:
                first = max(0, left - round(.09 * rate))
                last = max(0, right - round(.14 * rate))
                if first or last:
                    reader.setpos(first)
                    with io.BytesIO() as sink:
                        with wave.open(sink, "wb") as writer:
                            writer.setparams(params)
                            writer.writeframes(reader.readframes(params.nframes-first-last))
                        data = sink.getvalue()
                    left, right = left-first, right-last
    except (wave.Error, EOFError):
        return data, 0, 0
    return data, round(left * 1000 / rate), round(right * 1000 / rate)
