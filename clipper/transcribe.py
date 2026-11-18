"""Transcribe audio with faster-whisper, returning timed segments + words."""
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any

from . import config

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        device = config.WHISPER_DEVICE
        if device == "auto":
            try:
                import torch  # noqa
                device = "cuda" if (config.USE_GPU and torch.cuda.is_available()) else "cpu"
            except Exception:
                device = "cpu"

        # Ordered (device, compute) candidates — try GPU first, fall back gracefully.
        candidates = []
        if device == "cuda":
            candidates += [
                ("cuda", config.WHISPER_COMPUTE_CUDA),  # int8: works on Pascal (GTX 10xx)
                ("cuda", "int8"),
                ("cuda", "float32"),
            ]
        candidates.append(("cpu", config.WHISPER_COMPUTE))

        last = None
        for dev, comp in candidates:
            try:
                _model = WhisperModel(config.WHISPER_MODEL, device=dev, compute_type=comp)
                print(f"[transcribe] Whisper on {dev}/{comp}")
                break
            except Exception as e:  # noqa
                last = e
                print(f"[transcribe] {dev}/{comp} unavailable: {e}")
        if _model is None:
            raise last
    return _model


def transcribe(audio_path: Path, word_timestamps: bool = True) -> List[Dict[str, Any]]:
    """Return list of {start, end, text, words:[{start,end,word}]}."""
    model = _get_model()
    segments, _info = model.transcribe(
        str(audio_path), word_timestamps=word_timestamps, vad_filter=True
    )
    out: List[Dict[str, Any]] = []
    for seg in segments:
        words = []
        if word_timestamps and seg.words:
            words = [
                {"start": w.start, "end": w.end, "word": w.word} for w in seg.words
            ]
        out.append(
            {"start": seg.start, "end": seg.end, "text": seg.text.strip(), "words": words}
        )
    return out


def slice_words(segments: List[Dict], start: float, end: float) -> List[Dict]:
    """Collect word-level entries within [start, end], rebased to 0."""
    words = []
    for seg in segments:
        for w in seg.get("words", []):
            if w["end"] >= start and w["start"] <= end:
                words.append(
                    {
                        "start": max(0.0, w["start"] - start),
                        "end": max(0.0, w["end"] - start),
                        "word": w["word"].strip(),
                    }
                )
    return words
