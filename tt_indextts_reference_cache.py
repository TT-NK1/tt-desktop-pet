"""Store exact IndexTTS 2.0 reference conditioning locally across worker restarts."""
import hashlib
import sys
from pathlib import Path
from uuid import uuid4

from safetensors import SafetensorError
from safetensors.torch import load_file, save_file


FEATURES = {
    "cache_spk_cond": 3,
    "cache_s2mel_style": 2,
    "cache_s2mel_prompt": 3,
    "cache_mel": 3,
    "cache_emo_cond": 3,
}


class ReferenceCache:
    def __init__(self, directory, model, torch, infer_source):
        import librosa
        import torchaudio
        import transformers

        self.directory = directory
        weights = Path(model.model_dir)
        encoder = Path(model.reference_encoder_dir)
        signature = hashlib.sha256(repr((
            "desktop-index2-reference-v1", torch.__version__, torchaudio.__version__,
            transformers.__version__, librosa.__version__, torch.cuda.get_device_name(model.device),
        )).encode())
        for path in (weights / "config.yaml", infer_source, encoder / "config.json",
                     encoder / "preprocessor_config.json"):
            signature.update(path.read_bytes())
        for path in (weights / model.cfg.w2v_stat, weights / model.cfg.s2mel_checkpoint,
                     encoder / "model.safetensors", weights / "hf_cache" / "semantic_codec_model.safetensors",
                     weights / "hf_cache" / "campplus_cn_common.bin"):
            stat = path.stat()
            signature.update(repr((path.name, stat.st_size, stat.st_mtime_ns)).encode())
        self.signature = signature

    def path_for(self, reference):
        with reference.open("rb") as audio:
            key = self.signature.copy()
            key.update(hashlib.file_digest(audio, "sha256").digest())
        return self.directory / (key.hexdigest() + ".safetensors")

    @staticmethod
    def read(path):
        if not path.is_file():
            return None
        try:
            tensors = load_file(path, device="cpu")
            if (tensors.keys() == FEATURES.keys()
                    and all(tensor.ndim == FEATURES[name] and tensor.shape[0] == 1
                            and tensor.numel() and tensor.is_floating_point()
                            and tensor.isfinite().all().item() for name, tensor in tensors.items())):
                return tensors
        except (OSError, RuntimeError, ValueError, SafetensorError):
            pass
        print("IndexTTS reference cache could not be read; rebuilding it.", file=sys.stderr)
        return None

    @staticmethod
    def write(path, model):
        temporary = path.with_name(f".{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            save_file({name: getattr(model, name).to(device="cpu", copy=True).contiguous()
                       for name in FEATURES}, temporary)
            temporary.replace(path)
        except (OSError, RuntimeError, ValueError, SafetensorError):
            print("IndexTTS reference cache could not be saved.", file=sys.stderr)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
