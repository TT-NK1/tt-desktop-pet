"""Replaceable reference-audio synthesis boundary; no backend installed implicitly."""
from typing import Protocol
from pathlib import Path


class ReferenceSynthesizer(Protocol):
    """Implementations must use local files and must never download implicitly.

    Cloud implementations require a separate explicit upload consent flow.
    Reference conditioning is zero-shot synthesis, not model training.
    """

    def synthesize(self, reference: Path, text: str, output: Path) -> Path: ...


def generate(backend, reference, text, output, *, authorized=False):
    if not authorized:
        raise ValueError('请先确认声音属于本人或已获得明确授权，且用途在授权范围内。')
    if not text.strip():
        raise ValueError('请输入朗读文字。')
    if backend is None:
        raise RuntimeError('尚未配置参考音频合成模型；请先评估硬件、许可并批准依赖和模型下载。')
    reference, output = Path(reference), Path(output)
    if not reference.is_file():
        raise ValueError('参考音频不存在，请重新导入。')
    if reference.resolve() == output.resolve() or output.exists():
        raise ValueError('输出必须是新的文件，不能覆盖样本或已有文件。')
    result = Path(backend.synthesize(reference, text, output))
    if result.resolve() != output.resolve() or not result.is_file() or result.stat().st_size == 0:
        raise RuntimeError('模型没有产生有效输出；请检查模型配置后重试。')
    return result
