import gc
import sys
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

import torch

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import whisper  # noqa: E402

from .whisper_models import resolve_available_whisper_model_path, whisper_model_usage  # noqa: E402
from .transcription_languages import normalize_transcription_language  # noqa: E402

SUPPORTED_MODELS = {"tiny", "base", "small", "medium", "large", "large-v3", "turbo"}


class WhisperAdapter:
    """Headless adapter with a process-wide single-model CUDA lifecycle."""

    _cuda_lifecycle_lock = threading.RLock()
    _cuda_owner: weakref.ReferenceType["WhisperAdapter"] | None = None

    def __init__(self, device: str = "auto") -> None:
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError(f"Unsupported transcription device: {device}")
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was selected but is not available")
        self.device_setting = device
        self.effective_device = "cuda" if device == "auto" and torch.cuda.is_available() else "cpu" if device == "auto" else device
        self._lifecycle_lock = threading.RLock()
        self._model: object | None = None
        self._active_model_name: str | None = None
        self._model_path: Path | None = None
        self._verified_signature: tuple[int, int] | None = None
        self._last_load_metadata: dict[str, object] | None = None

    @property
    def active_model_name(self) -> str | None:
        with self._lifecycle_lock:
            return self._active_model_name

    @property
    def cached_model_count(self) -> int:
        with self._lifecycle_lock:
            return int(self._model is not None)

    @property
    def last_load_metadata(self) -> dict[str, object] | None:
        with self._lifecycle_lock:
            return dict(self._last_load_metadata) if self._last_load_metadata else None

    @contextmanager
    def _operation_lock(self) -> Iterator[None]:
        # All CUDA adapters in this process share the lock. This prevents one
        # adapter from evicting a model while another adapter is using it.
        if self.effective_device == "cuda":
            with self._cuda_lifecycle_lock:
                with self._lifecycle_lock:
                    yield
        else:
            with self._lifecycle_lock:
                yield

    def _compute_type(self, fp16: bool | None) -> str:
        return "float16" if self.effective_device == "cuda" and fp16 is not False else "float32"

    def _vram_info(self) -> tuple[int | None, int | None]:
        if self.effective_device != "cuda" or not torch.cuda.is_available():
            return None, None
        try:
            free, total = torch.cuda.mem_get_info()
            return int(free), int(total)
        except (RuntimeError, TypeError):
            return None, None

    def _set_load_metadata(self, requested_model: str, fp16: bool | None) -> None:
        free, total = self._vram_info()
        self._last_load_metadata = {
            "requested_model": requested_model,
            "active_model": self._active_model_name,
            "device": self.effective_device,
            "compute_type": self._compute_type(fp16),
            "vram_free_bytes_before_load": free,
            "vram_total_bytes_before_load": total,
        }

    def _cuda_cleanup(self) -> None:
        if self.effective_device == "cuda" and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _release_model_locked(self) -> None:
        self._model = None
        self._active_model_name = None
        self._model_path = None
        self._verified_signature = None
        gc.collect()
        self._cuda_cleanup()

        owner = self._cuda_owner() if self._cuda_owner is not None else None
        if owner is self:
            type(self)._cuda_owner = None

    def release_cache(self) -> None:
        """Release the active model and any cached CUDA allocator blocks."""
        with self._operation_lock():
            self._release_model_locked()

    def _evict_other_cuda_owner_locked(self) -> None:
        if self.effective_device != "cuda":
            return
        owner = self._cuda_owner() if self._cuda_owner is not None else None
        if owner is not None and owner is not self:
            # The process-wide CUDA lock is already held, so the owner cannot
            # be running inference. Acquire its instance lock before mutation.
            with owner._lifecycle_lock:
                owner._release_model_locked()
        type(self)._cuda_owner = weakref.ref(self)

    def _load_model_locked(
        self,
        model_name: str,
        cancel_callback: Callable[[], bool] | None,
        fp16: bool | None,
    ) -> object:
        if model_name not in SUPPORTED_MODELS:
            raise ValueError(f"Unsupported Whisper model: {model_name}")

        checkpoint_path = resolve_available_whisper_model_path(model_name)
        checkpoint_stat = checkpoint_path.stat()
        current_signature = (checkpoint_stat.st_size, checkpoint_stat.st_mtime_ns)
        if (
            self._model is not None
            and self._active_model_name == model_name
            and self._model_path == checkpoint_path
            and self._verified_signature == current_signature
        ):
            self._set_load_metadata(model_name, fp16)
            return self._model

        if self._model is not None:
            self._release_model_locked()
        self._evict_other_cuda_owner_locked()
        self._set_load_metadata(model_name, fp16)

        try:
            with whisper_model_usage(model_name, "checkpoint-load"):
                model = whisper.load_model(
                    str(checkpoint_path),
                    device=self.effective_device,
                    cancel_callback=cancel_callback,
                )
        except torch.cuda.OutOfMemoryError as exc:
            # Drop traceback frames that may still reference partially-created
            # CUDA tensors before asking PyTorch to release allocator blocks.
            cleaned_error = exc.with_traceback(None)
            self._release_model_locked()
            if self._last_load_metadata is not None:
                self._last_load_metadata["active_model"] = None
            raise cleaned_error from None

        self._model = model
        self._active_model_name = model_name
        self._model_path = checkpoint_path
        self._verified_signature = current_signature
        if self._last_load_metadata is not None:
            self._last_load_metadata["active_model"] = model_name
        return model

    def load_model(
        self,
        model_name: str,
        cancel_callback: Callable[[], bool] | None = None,
        fp16: bool | None = None,
    ) -> object:
        with self._operation_lock():
            return self._load_model_locked(model_name, cancel_callback, fp16)

    def transcribe(
        self,
        audio_path: Path,
        model_name: str,
        language: str | None,
        progress_callback: Callable[[int], None] | None = None,
        cancel_callback: Callable[[], bool] | None = None,
        fp16: bool | None = None,
        beam_size: int | None = None,
        temperature: float | None = None,
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
        best_of: int | None = None,
        condition_on_previous_text: bool = True,
        no_speech_threshold: float | None = 0.6,
    ) -> dict:
        with self._operation_lock():
            model = self._load_model_locked(model_name, cancel_callback, fp16)
            selected_language = normalize_transcription_language(language)
            try:
                with whisper_model_usage(model_name, "model-inference"):
                    result = model.transcribe(
                        str(audio_path),
                        language=selected_language,
                        task="transcribe",
                        fp16=(str(model.device).startswith("cuda") if fp16 is None else fp16 and str(model.device).startswith("cuda")),
                        beam_size=beam_size,
                        best_of=best_of,
                        temperature=temperature if temperature is not None else (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                        initial_prompt=initial_prompt or None,
                        word_timestamps=word_timestamps,
                        condition_on_previous_text=condition_on_previous_text,
                        no_speech_threshold=no_speech_threshold,
                        verbose=None,
                        cancel_func=cancel_callback or (lambda: False),
                        progress_callback=progress_callback,
                    )
            except torch.cuda.OutOfMemoryError as exc:
                cleaned_error = exc.with_traceback(None)
                self._release_model_locked()
                if self._last_load_metadata is not None:
                    self._last_load_metadata["active_model"] = None
                raise cleaned_error from None
        if result is False:
            raise InterruptedError("Transcription was interrupted")
        return result
