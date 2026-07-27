#!/usr/bin/env python3
"""Adapter for the repository's current local Whisper implementation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

CHECKPOINT_NAMES = {
    "tiny": "tiny.pt",
    "base": "base.pt",
    "small": "small.pt",
    "medium": "medium.pt",
    "large": "large-v3.pt",
}


def emit(event: str, **values: object) -> None:
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def main() -> int:
    audio = Path(os.environ["BENCHMARK_AUDIO"])
    model_name = os.environ["BENCHMARK_MODEL"]
    language = os.environ.get("BENCHMARK_LANGUAGE", "auto")
    checkpoint_override = os.environ.get("BENCHMARK_CHECKPOINT")
    checkpoint = Path(checkpoint_override) if checkpoint_override else PROJECT_ROOT / "storage" / "models" / "whisper" / CHECKPOINT_NAMES[model_name]
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Local checkpoint not found: {checkpoint}")

    # Prerecorded input is complete when this offline adapter starts inference.
    emit("audio_end")
    from src import whisper

    model = whisper.load_model(str(checkpoint), device="cuda" if _cuda_available() else "cpu")
    result = model.transcribe(
        str(audio),
        task="transcribe",
        language=None if language in {"auto", "id-en"} else language,
        fp16=_cuda_available(),
        verbose=None,
        cancel_func=lambda: False,
    )
    emit("final", text=str(result.get("text", "")).strip())
    return 0


def _cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except ImportError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
