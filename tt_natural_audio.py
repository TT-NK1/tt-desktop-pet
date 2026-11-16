"""Smooth low-energy edges and join generated clauses locally."""
import subprocess

import numpy as np
import soundfile as sf

from tt_natural_speech import pause_after
from tt_speech_segments import PauseController
from tt_speech_wave import quiet_edge_frames

QUIET_PEAK = 10 ** (-39 / 20)
FOLLOWUP_TEMPO = 1.10


def compact_followup_pauses(path, ffmpeg):
    """Clarify the first syllable and tighten the question after its opening pause."""
    data, rate = sf.read(path, dtype="float32")
    sf.write(path, compact_audio(data, rate, ffmpeg), rate, subtype="PCM_16")


def compact_audio(data, rate, ffmpeg):
    if data.ndim != 1:
        raise ValueError("Expected mono audio")
    frame = rate // 100
    frames = data[:len(data) // frame * frame].reshape(-1, frame)
    quiet = np.max(np.abs(frames), axis=1) < QUIET_PEAK
    gaps = np.flatnonzero(np.diff(np.r_[False, quiet, False])).reshape(-1, 2)
    voiced = np.flatnonzero(~quiet)
    if not len(voiced):
        return data
    onset = voiced[0]
    # The comma after the opening pronoun and “呢” produces the first substantial gap.
    # Look only near the opening, so pauses later in the question stay short.
    comma_gap = next(((left, right) for left, right in gaps
                      if 24 <= left - onset <= 75 and right - left >= 16), None)
    clarify_opening_pronoun(data, frame, early=True)
    question_start = (comma_gap[1] - 1) * frame if comma_gap is not None else None
    removed_before_question = 0
    edge_guard, minimum_gap = 15, 9
    fade = max(1, rate // 250)
    mix = np.linspace(0, 1, fade, dtype=np.float32)
    parts = []
    start = 0
    for left, right in gaps:
        keep_on_each_side = 18 if comma_gap is not None and left == comma_gap[0] else 2
        if (left < edge_guard or right > len(quiet) - edge_guard
                or right - left < max(minimum_gap, 2 * keep_on_each_side + 1)):
            continue
        cut_start = (left + keep_on_each_side) * frame
        cut_end = (right - keep_on_each_side) * frame
        parts.extend((data[start:cut_start - fade],
                      data[cut_start - fade:cut_start] * (1 - mix)
                      + data[cut_end:cut_end + fade] * mix))
        start = cut_end + fade
        if question_start is not None and start < question_start:
            removed_before_question += cut_end - cut_start + fade
    if parts:
        data = np.concatenate([*parts, data[start:]])
    if question_start is not None:
        start = question_start - removed_before_question
        sped = speed_up_question(data[start:], rate, ffmpeg)
        if len(sped) >= fade:
            sped[:fade] = data[start:start + fade] * (1 - mix) + sped[:fade] * mix
            data = np.concatenate([data[:start], sped])
    return data


def speed_up_question(data, rate, ffmpeg):
    """Adjust only the question's tempo using the existing pitch-preserving filter."""
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
         "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
         "-af", f"atempo={FOLLOWUP_TEMPO}", "-f", "f32le", "pipe:1"],
        input=data.astype("<f4", copy=False).tobytes(), stdout=subprocess.PIPE,
        check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return np.frombuffer(result.stdout, dtype="<f4").copy()


def clarify_opening_pronoun(data, frame, *, early=False):
    """Raise a soft pronoun onset with a taper into the rest of the syllable."""
    blocks = data[:len(data) // frame * frame].reshape(-1, frame)
    peaks = np.max(np.abs(blocks), axis=1)
    starts = np.flatnonzero((peaks[:-1] >= .008) & (peaks[1:] >= .008))
    if not len(starts):
        return
    onset = starts[0] * frame
    if onset + 35 * frame > len(data):
        return
    attack, ref_start, ref_end, release, max_db = (5, 5, 15, 14, 5.5) if early else (10, 16, 35, 20, 3.5)
    first = data[onset:onset + attack * frame]
    next_syllables = data[onset + ref_start * frame:onset + ref_end * frame]
    level = np.sqrt(np.mean(first ** 2))
    target = np.sqrt(np.mean(next_syllables ** 2))
    window = data[max(0, onset - frame):onset + release * frame]
    gain = min(10 ** (max_db / 20), target / level, .98 / np.max(np.abs(window)))
    if gain > 1:
        weights = np.interp(np.arange(len(window)),
                            [0, frame, (attack + 1) * frame, len(window) - 1], [1, gain, gain, 1])
        window *= weights


def join_clauses(files, clauses, path, pauses=None, *, tight_joins=()):
    contents = [sf.read(source, dtype="float32") for source in files]
    if any(rate!=22050 or data.ndim!=1 for data,rate in contents):
        raise ValueError("Expected mono 22.05 kHz audio")
    sound = join_audio([data for data,_ in contents], clauses, 22050, pauses, tight_joins=tight_joins)
    sf.write(path, sound, 22050, subtype="PCM_16")


def join_audio(contents, clauses, rate, pauses=None, *, tight_joins=(), pause_metrics=None):
    arrays, prepared = [], []
    for index, original in enumerate(contents):
        data = original.copy()
        if index + 1 in tight_joins and clauses[index].startswith("我"):
            clarify_opening_pronoun(data, rate // 100)
        leading = quiet_edge_frames(data, rate, threshold=.006)
        trailing = quiet_edge_frames(data[::-1], rate, threshold=.006)
        head = max(0, leading - round(.065 * rate)) if index else 0
        tail = max(0, trailing - round(.095 * rate)) if index + 1 < len(contents) else 0
        clipped = data[head:len(data) - tail]
        fade = min(rate // 200, len(clipped) // 2)
        if fade and head:
            clipped[:fade] *= np.linspace(0, 1, fade)
        if fade and tail:
            clipped[-fade:] *= np.linspace(1, 0, fade)
        prepared.append((clipped, (leading-head)*1000/rate, (trailing-tail)*1000/rate))
    for index, (data, _, tail_ms) in enumerate(prepared):
        arrays.append(data)
        if index + 1 < len(prepared):
            target = pauses[index] if pauses is not None else pause_after(clauses[index])
            head_ms = prepared[index+1][1]
            gap = PauseController.additional(target, head_ms, tail_ms)
            if pause_metrics is not None:
                pause_metrics.append(dict(target_ms=target, tail_ms=round(tail_ms), head_ms=round(head_ms),
                                          inserted_ms=gap, total_ms=round(tail_ms+head_ms+gap)))
            if gap:
                arrays.append(np.zeros(round(rate * gap / 1000), dtype="float32"))
    return np.concatenate(arrays)
