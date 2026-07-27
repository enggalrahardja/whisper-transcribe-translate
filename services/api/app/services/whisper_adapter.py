import sys
from pathlib import Path
from typing import Callable

import torch

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import whisper  # noqa: E402

from .whisper_models import resolve_available_whisper_model_path, whisper_model_usage  # noqa: E402

SUPPORTED_MODELS = {"tiny", "base", "small", "medium", "large"}


class WhisperAdapter:
    """Headless adapter around the project's Whisper implementation."""

    def __init__(self, device: str = "auto") -> None:
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError(f"Unsupported transcription device: {device}")
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was selected but is not available")
        self.device_setting = device
        self.effective_device = "cuda" if device == "auto" and torch.cuda.is_available() else "cpu" if device == "auto" else device
        self._models: dict[str, object] = {}
        self._model_paths: dict[str, Path] = {}
        self._verified_signatures: dict[str, tuple[int, int]] = {}

    def load_model(
        self,
        model_name: str,
        cancel_callback: Callable[[], bool] | None = None,
    ) -> object:
        if model_name not in SUPPORTED_MODELS:
            raise ValueError(f"Unsupported Whisper model: {model_name}")

        model = self._models.get(model_name)
        checkpoint_path = self._model_paths.get(model_name)
        verified_signature = self._verified_signatures.get(model_name)
        try:
            checkpoint_stat = checkpoint_path.stat() if checkpoint_path is not None else None
            current_signature = (
                (checkpoint_stat.st_size, checkpoint_stat.st_mtime_ns)
                if checkpoint_stat is not None
                else None
            )
        except OSError:
            current_signature = None

        if model is None or current_signature != verified_signature:
            with whisper_model_usage(model_name, "checkpoint-load"):
                checkpoint_path = resolve_available_whisper_model_path(model_name)
                checkpoint_stat = checkpoint_path.stat()
                current_signature = (checkpoint_stat.st_size, checkpoint_stat.st_mtime_ns)

                if model is None:
                    model = whisper.load_model(
                        str(checkpoint_path),
                        device=self.effective_device,
                        cancel_callback=cancel_callback,
                    )
                    self._models[model_name] = model
        self._model_paths[model_name] = checkpoint_path
        self._verified_signatures[model_name] = current_signature
        return model

    def transcribe(
        self,
        audio_path: Path,
        model_name: str,
        language: str,
        progress_callback: Callable[[int], None] | None = None,
        cancel_callback: Callable[[], bool] | None = None,
        fp16: bool | None = None,
        beam_size: int | None = None,
        temperature: float | None = None,
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
    ) -> dict:
        model = self.load_model(model_name)

        selected_language = None if language == "auto" else language
        with whisper_model_usage(model_name, "model-inference"):
            result = model.transcribe(
                str(audio_path),
                language=selected_language,
                task="transcribe",
                fp16=(str(model.device).startswith("cuda") if fp16 is None else fp16 and str(model.device).startswith("cuda")),
                beam_size=beam_size,
                temperature=temperature if temperature is not None else (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                initial_prompt=initial_prompt or None,
                word_timestamps=word_timestamps,
                verbose=None,
                cancel_func=cancel_callback or (lambda: False),
                progress_callback=progress_callback,
            )
        if result is False:
            raise InterruptedError("Transcription was interrupted")
        return result
