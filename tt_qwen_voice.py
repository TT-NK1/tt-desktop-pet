"""Local voice cloning for Japanese and Korean in TT's GPU worker."""
import io
import sys
from pathlib import Path


LANGUAGES = {'ja': 'Japanese', 'ko': 'Korean'}


class ForeignVoice:
    def __init__(self, runtime):
        import torch

        sys.path.insert(0, str(runtime / 'python'))
        from qwen_tts import Qwen3TTSModel

        self.model = Qwen3TTSModel.from_pretrained(
            str(runtime / 'model'), device_map='cuda:0', dtype=torch.bfloat16,
            attn_implementation='sdpa', local_files_only=True,
        )
        self.reference_key = None
        self.reference_prompt = None

    def render(self, request, stopping_criteria):
        reference = Path(request['reference'])
        state = reference.stat()
        transcript = request.get('reference_text', '').strip()
        key = (reference, state.st_size, state.st_mtime_ns, transcript)
        if key != self.reference_key:
            self.reference_prompt = self.model.create_voice_clone_prompt(
                ref_audio=str(reference), ref_text=transcript or None, x_vector_only_mode=not transcript,
            )
            self.reference_key = key
        text = request['text']
        waves, rate = self.model.generate_voice_clone(
            text=text, language=LANGUAGES[request['language']], voice_clone_prompt=self.reference_prompt,
            non_streaming_mode=True, stopping_criteria=stopping_criteria,
            repetition_penalty=1.08, max_new_tokens=min(900, max(120, len(text) * 10 + 60)),
        )
        return waves[0], rate


def to_wave(audio, rate):
    import numpy as np
    import soundfile as sf

    if not audio.size or not np.isfinite(audio).all():
        raise ValueError('生成的音频为空或无效')
    with io.BytesIO() as stream:
        sf.write(stream, audio, rate, format='WAV', subtype='PCM_16')
        return stream.getvalue()
