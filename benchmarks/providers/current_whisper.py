#!/usr/bin/env python3
"""Adapter for the repository's current local Whisper implementation."""

from __future__ import annotations

import json
import hashlib
import os
import sys
import wave
from time import perf_counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

CHECKPOINT_NAMES = {
    "tiny": "tiny.pt",
    "base": "base.pt",
    "small": "small.pt",
    "medium": "medium.pt",
    "large": "large-v3.pt",
    "large-v3": "large-v3.pt",
    "large-v3-turbo": "large-v3-turbo.pt",
}


def emit(event: str, **values: object) -> None:
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def main() -> int:
    audio = Path(os.environ["BENCHMARK_AUDIO"])
    model_name = os.environ["BENCHMARK_MODEL"]
    language = os.environ.get("BENCHMARK_LANGUAGE", "auto")
    beam_size = int(os.environ.get("BENCHMARK_BEAM_SIZE", "5"))
    checkpoint_override = os.environ.get("BENCHMARK_CHECKPOINT")
    checkpoint = Path(checkpoint_override) if checkpoint_override else PROJECT_ROOT / "storage" / "models" / "whisper" / CHECKPOINT_NAMES[model_name]
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Local checkpoint not found: {checkpoint}")

    # Prerecorded input is complete when this offline adapter starts inference.
    emit("audio_end")
    from src import whisper

    load_started = perf_counter()
    model = whisper.load_model(str(checkpoint), device="cuda" if _cuda_available() else "cpu")
    emit("model_loaded", latency_ms=(perf_counter() - load_started) * 1000,
         checkpoint=checkpoint.name, checkpoint_sha256=_sha256(checkpoint),
         device="cuda" if _cuda_available() else "cpu", compute_type="float16" if _cuda_available() else "float32",
         beam_size=beam_size)
    result = model.transcribe(
        _pcm_float32(audio),
        task="transcribe",
        language=None if language in {"auto", "id-en"} else language,
        fp16=_cuda_available(),
        temperature=0.0,
        beam_size=beam_size,
        verbose=None,
        cancel_func=lambda: False,
    )
    emit("final", text=str(result.get("text", "")).strip())
    return 0


def _pcm_float32(path: Path):
    """Read benchmark PCM WAV directly so the suite does not require ffmpeg."""
    import numpy as np
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 16_000:
            raise ValueError("benchmark adapter requires PCM16 mono 16 kHz WAV")
        frames = source.readframes(source.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except ImportError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
