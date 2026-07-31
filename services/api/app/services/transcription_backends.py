"""Backend-neutral Whisper inference, capability checks, and model lifecycle."""

from __future__ import annotations

import gc
import importlib.metadata
import importlib.util
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol

import torch

from .whisper_adapter import WhisperAdapter

BACKENDS = ("pytorch", "faster-whisper")
MODELS = ("tiny", "base", "small", "medium", "large-v3")
LEGACY_MODEL_ALIASES = {"large": "large-v3"}
COMPUTE_MATRIX = {
    "pytorch": {
        "cuda": ("float16", "float32"),
        "cpu": ("float32",),
    },
    "faster-whisper": {
        "cuda": ("float16", "int8_float16", "int8"),
        "cpu": ("int8", "float32"),
    },
}


class TranscriptionBackendError(RuntimeError):
    def __init__(self, code: str, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


class BackendOutOfMemoryError(TranscriptionBackendError):
    pass


@dataclass(frozen=True)
class BackendConfig:
    backend: str
    model: str
    device: str
    compute_type: str

    @property
    def identity(self) -> str:
        return f"{self.backend}:{self.model}:{self.device}:{self.compute_type}"


@dataclass(frozen=True)
class TranscriptionOptions:
    language: str
    beam_size: int = 5
    best_of: int | None = None
    temperature: float = 0.0
    initial_prompt: str = ""
    word_timestamps: bool = False
    condition_on_previous_text: bool = True
    no_speech_threshold: float | None = 0.6
    progress_callback: Callable[[int], None] | None = None
    cancel_callback: Callable[[], bool] | None = None


class TranscriptionBackend(Protocol):
    def load_model(self, config: BackendConfig) -> object: ...
    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> dict: ...
    def unload_model(self) -> None: ...
    def get_runtime_metadata(self) -> dict[str, object]: ...


def canonical_model_name(model: str) -> str:
    return LEGACY_MODEL_ALIASES.get(model, model)


def pytorch_model_name(model: str) -> str:
    return "large" if canonical_model_name(model) == "large-v3" else canonical_model_name(model)


def _package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _faster_whisper_available() -> tuple[bool, str | None]:
    if importlib.util.find_spec("faster_whisper") is None:
        return False, "The faster-whisper dependency is not installed"
    if importlib.util.find_spec("ctranslate2") is None:
        return False, "The CTranslate2 dependency is not installed"
    return True, None


def runtime_capabilities() -> dict[str, object]:
    faster_available, faster_reason = _faster_whisper_available()
    torch_cuda_available = bool(torch.cuda.is_available())
    ctranslate_cuda_available = False
    faster_compute: dict[str, list[str]] = {"cpu": [], "cuda": []}
    if faster_available:
        try:
            import ctranslate2

            ctranslate_cuda_available = ctranslate2.get_cuda_device_count() > 0
            for device in ("cpu", "cuda"):
                if device == "cuda" and not ctranslate_cuda_available:
                    continue
                supported = set(ctranslate2.get_supported_compute_types(device))
                faster_compute[device] = [
                    value for value in COMPUTE_MATRIX["faster-whisper"][device]
                    if value in supported
                ]
        except Exception as exc:
            faster_available = False
            faster_reason = f"CTranslate2 capability detection failed: {exc}"

    pytorch_compute = {
        "cpu": list(COMPUTE_MATRIX["pytorch"]["cpu"]),
        "cuda": list(COMPUTE_MATRIX["pytorch"]["cuda"]) if torch_cuda_available else [],
    }
    return {
        "backends": [
            {"id": "pytorch", "label": "Whisper PyTorch", "available": True, "reason": None},
            {"id": "faster-whisper", "label": "faster-whisper", "available": faster_available, "reason": faster_reason},
        ],
        "devices": [
            {"id": "cpu", "label": "CPU", "available": True},
            {"id": "cuda", "label": "CUDA", "available": torch_cuda_available or ctranslate_cuda_available},
        ],
        "compute_types": {
            "pytorch": pytorch_compute,
            "faster-whisper": faster_compute,
        },
        "models": list(MODELS),
        "recommended": {
            "backend": "faster-whisper",
            "model": "large-v3",
            "device": "cuda",
            "compute_type": "int8_float16",
        },
    }


def resolve_backend_config(backend: str, model: str, device: str, compute_type: str) -> BackendConfig:
    if backend not in BACKENDS:
        raise TranscriptionBackendError("backend_unsupported", f"Unsupported transcription backend: {backend}")
    canonical_model = canonical_model_name(model)
    if canonical_model not in MODELS:
        raise TranscriptionBackendError("model_unsupported", f"Unsupported Whisper model: {model}")

    capabilities = runtime_capabilities()
    backend_capability = next(item for item in capabilities["backends"] if item["id"] == backend)
    if not backend_capability["available"]:
        raise TranscriptionBackendError(
            "dependency_unavailable",
            f'{backend} is unavailable: {backend_capability["reason"]}',
        )

    effective_device = device
    if device == "auto":
        effective_device = "cuda" if any(
            item["id"] == "cuda" and item["available"] for item in capabilities["devices"]
        ) else "cpu"
    if effective_device not in {"cpu", "cuda"}:
        raise TranscriptionBackendError("device_unsupported", f"Unsupported transcription device: {device}")
    device_available = any(
        item["id"] == effective_device and item["available"] for item in capabilities["devices"]
    )
    if not device_available:
        raise TranscriptionBackendError(
            "cuda_unavailable",
            f"CUDA is not available for backend {backend}; select CPU with a supported compute type",
        )

    valid_compute_types = capabilities["compute_types"][backend][effective_device]
    if effective_device == "cuda" and not valid_compute_types:
        raise TranscriptionBackendError(
            "cuda_unavailable",
            f"CUDA is not available for backend {backend}; select CPU with a supported compute type",
        )
    effective_compute = compute_type
    if compute_type == "auto":
        if backend == "faster-whisper" and effective_device == "cuda" and "int8_float16" in valid_compute_types:
            effective_compute = "int8_float16"
        elif backend == "pytorch" and effective_device == "cuda":
            effective_compute = "float16"
        else:
            effective_compute = valid_compute_types[0] if valid_compute_types else ""
    if effective_compute not in valid_compute_types:
        choices = ", ".join(valid_compute_types) or "none available at runtime"
        raise TranscriptionBackendError(
            "compute_type_unsupported",
            f"Invalid compute type {compute_type} for backend {backend} on {effective_device}. Valid choices: {choices}",
        )
    return BackendConfig(backend, canonical_model, effective_device, effective_compute)


class PytorchWhisperBackend:
    def __init__(self) -> None:
        self.adapter: WhisperAdapter | None = None
        self.config: BackendConfig | None = None

    def load_model(self, config: BackendConfig) -> object:
        if self.adapter is None or self.adapter.effective_device != config.device:
            if self.adapter is not None:
                self.adapter.release_cache()
            self.adapter = WhisperAdapter(config.device)
        self.config = config
        return self.adapter.load_model(
            pytorch_model_name(config.model),
            fp16=config.compute_type == "float16",
        )

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> dict:
        if self.adapter is None or self.config is None:
            raise RuntimeError("PyTorch Whisper model is not loaded")
        return self.adapter.transcribe(
            audio_path,
            model_name=pytorch_model_name(self.config.model),
            language=options.language,
            progress_callback=options.progress_callback,
            cancel_callback=options.cancel_callback,
            fp16=self.config.compute_type == "float16",
            beam_size=options.beam_size,
            best_of=options.best_of,
            temperature=options.temperature,
            initial_prompt=options.initial_prompt,
            word_timestamps=options.word_timestamps,
            condition_on_previous_text=options.condition_on_previous_text,
            no_speech_threshold=options.no_speech_threshold,
        )

    def unload_model(self) -> None:
        if self.adapter is not None:
            self.adapter.release_cache()
        self.adapter = None
        self.config = None

    def get_runtime_metadata(self) -> dict[str, object]:
        return self.adapter.last_load_metadata or {} if self.adapter is not None else {}


class FasterWhisperBackend:
    def __init__(self) -> None:
        self.model: object | None = None
        self.config: BackendConfig | None = None

    def load_model(self, config: BackendConfig) -> object:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionBackendError(
                "dependency_unavailable", "faster-whisper is not installed", stage="load"
            ) from exc
        try:
            self.model = WhisperModel(
                config.model,
                device=config.device,
                compute_type=config.compute_type,
            )
        except Exception as exc:
            if _is_oom(exc):
                self.unload_model()
                raise BackendOutOfMemoryError("oom_load", str(exc), stage="load") from exc
            message = str(exc).lower()
            if any(marker in message for marker in ("download", "huggingface", "connection", "repository not found")):
                raise TranscriptionBackendError(
                    "model_download_failed", f"faster-whisper model download failed: {exc}", stage="load"
                ) from exc
            if _is_dependency_incompatible(exc):
                raise TranscriptionBackendError(
                    "dependency_incompatible", f"faster-whisper dependency is incompatible: {exc}", stage="load"
                ) from exc
            raise
        self.config = config
        return self.model

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> dict:
        if self.model is None:
            raise RuntimeError("faster-whisper model is not loaded")
        if options.cancel_callback and options.cancel_callback():
            raise InterruptedError("Transcription was interrupted")
        kwargs: dict[str, object] = {
            "language": None if options.language == "auto" else options.language,
            "task": "transcribe",
            "beam_size": options.beam_size,
            "temperature": options.temperature,
            "initial_prompt": options.initial_prompt or None,
            "word_timestamps": options.word_timestamps,
            "condition_on_previous_text": options.condition_on_previous_text,
            "vad_filter": options.no_speech_threshold is not None,
        }
        if options.no_speech_threshold is not None:
            kwargs["no_speech_threshold"] = options.no_speech_threshold
        try:
            segment_iterator, info = self.model.transcribe(str(audio_path), **kwargs)
            normalized_segments: list[dict[str, object]] = []
            for index, segment in enumerate(segment_iterator):
                if options.cancel_callback and options.cancel_callback():
                    raise InterruptedError("Transcription was interrupted")
                words = None
                if getattr(segment, "words", None):
                    words = [
                        {
                            "word": word.word,
                            "start": word.start,
                            "end": word.end,
                            "probability": word.probability,
                        }
                        for word in segment.words
                    ]
                normalized = {
                    "id": getattr(segment, "id", index),
                    "seek": getattr(segment, "seek", 0),
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": str(segment.text),
                    "tokens": list(getattr(segment, "tokens", ()) or ()),
                    "temperature": getattr(segment, "temperature", options.temperature),
                    "avg_logprob": getattr(segment, "avg_logprob", None),
                    "compression_ratio": getattr(segment, "compression_ratio", None),
                    "no_speech_prob": getattr(segment, "no_speech_prob", None),
                }
                if words is not None:
                    normalized["words"] = words
                normalized_segments.append(normalized)
            if options.progress_callback:
                options.progress_callback(100)
        except Exception as exc:
            if _is_oom(exc):
                self.unload_model()
                raise BackendOutOfMemoryError("oom_inference", str(exc), stage="inference") from exc
            if _is_dependency_incompatible(exc):
                raise TranscriptionBackendError(
                    "dependency_incompatible", f"faster-whisper dependency is incompatible: {exc}", stage="inference"
                ) from exc
            raise
        return {
            "text": "".join(str(segment["text"]) for segment in normalized_segments).strip(),
            "segments": normalized_segments,
            "language": str(getattr(info, "language", options.language)),
            "language_probability": getattr(info, "language_probability", None),
            "duration": float(getattr(info, "duration", 0.0)),
        }

    def unload_model(self) -> None:
        self.model = None
        self.config = None
        gc.collect()

    def get_runtime_metadata(self) -> dict[str, object]:
        return {}


def _is_oom(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "out of memory" in message or "cuda_error_out_of_memory" in message


def _is_dependency_incompatible(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ("cudnn", "cublas", "library", "symbol not found"))


class TranscriptionBackendManager:
    """Process-wide single active model cache across both implementations."""

    _operation_lock = threading.RLock()
    _owner: weakref.ReferenceType["TranscriptionBackendManager"] | None = None

    def __init__(self) -> None:
        self._backend: TranscriptionBackend | None = None
        self._config: BackendConfig | None = None
        self._metadata: dict[str, object] = {"model_status": "not_loaded"}

    @property
    def active_model_name(self) -> str | None:
        return self._config.model if self._config else None

    @property
    def cached_model_count(self) -> int:
        return int(self._backend is not None)

    @property
    def effective_device(self) -> str | None:
        return self._config.device if self._config else None

    @property
    def device_setting(self) -> str | None:
        return self._metadata.get("requested_device") if self._metadata else None

    @property
    def last_load_metadata(self) -> dict[str, object] | None:
        return dict(self._metadata) if self._metadata else None

    def _release_locked(self, status: str = "released") -> None:
        released_config = self._config
        if self._backend is not None:
            self._backend.unload_model()
        self._backend = None
        self._config = None
        gc.collect()
        if released_config is not None and released_config.backend == "pytorch" and released_config.device == "cuda" and torch.cuda.is_available():
            torch.cuda.empty_cache()
        self._metadata["model_status"] = status
        owner = self._owner() if self._owner else None
        if owner is self:
            type(self)._owner = None

    def release_cache(self) -> None:
        with self._operation_lock:
            self._release_locked()

    def load_model(
        self,
        backend: str,
        model: str,
        device: str,
        compute_type: str,
        cancel_callback: Callable[[], bool] | None = None,
    ) -> object:
        with self._operation_lock:
            requested = {"backend": backend, "model": model, "device": device, "compute_type": compute_type}
            config = resolve_backend_config(backend, model, device, compute_type)
            if cancel_callback and cancel_callback():
                raise InterruptedError("Transcription was interrupted")
            owner = self._owner() if self._owner else None
            if owner is not None and owner is not self:
                owner._release_locked()
            if self._backend is not None and self._config == config:
                self._metadata.update({"model_status": "ready", "cache_reused": True})
                return getattr(self._backend, "model", None) or getattr(self._backend, "adapter", self._backend)
            if self._backend is not None:
                self._release_locked()
            type(self)._owner = weakref.ref(self)
            self._metadata = {
                "requested_backend": requested["backend"],
                "active_backend": None,
                "requested_model": requested["model"],
                "active_model": None,
                "requested_device": requested["device"],
                "device": config.device,
                "requested_compute_type": requested["compute_type"],
                "compute_type": config.compute_type,
                "model_status": "loading",
                "cache_identity": config.identity,
                "cache_reused": False,
                "backend_library_version": _package_version("faster-whisper") if backend == "faster-whisper" else torch.__version__,
            }
            if config.device == "cuda" and torch.cuda.is_available():
                try:
                    free_before, total_before = torch.cuda.mem_get_info()
                    self._metadata.update({
                        "vram_free_bytes_before_load": int(free_before),
                        "vram_total_bytes_before_load": int(total_before),
                    })
                except (RuntimeError, TypeError):
                    pass
            started = monotonic()
            selected: TranscriptionBackend = FasterWhisperBackend() if backend == "faster-whisper" else PytorchWhisperBackend()
            try:
                model_object = selected.load_model(config)
            except torch.cuda.OutOfMemoryError as exc:
                self._metadata.update({"model_status": "failed", "failure_stage": "load"})
                self._release_locked("failed")
                raise BackendOutOfMemoryError("oom_load", str(exc), stage="load") from exc
            except Exception:
                self._metadata.update({"model_status": "failed", "failure_stage": "load"})
                self._release_locked("failed")
                raise
            self._backend = selected
            self._config = config
            backend_metadata = selected.get_runtime_metadata()
            vram_after: int | None = None
            if config.device == "cuda" and torch.cuda.is_available():
                try:
                    vram_after = int(torch.cuda.mem_get_info()[0])
                except (RuntimeError, TypeError):
                    pass
            self._metadata.update({
                "active_backend": config.backend,
                "active_model": config.model,
                "model_status": "ready",
                "model_load_duration_seconds": round(monotonic() - started, 6),
                "vram_free_bytes_before_load": self._metadata.get("vram_free_bytes_before_load", backend_metadata.get("vram_free_bytes_before_load")),
                "vram_total_bytes_before_load": self._metadata.get("vram_total_bytes_before_load", backend_metadata.get("vram_total_bytes_before_load")),
                "vram_free_bytes_after_load": vram_after,
            })
            return model_object

    def transcribe(
        self,
        audio_path: Path,
        *,
        backend: str,
        model_name: str,
        device: str,
        compute_type: str,
        **kwargs: object,
    ) -> dict:
        with self._operation_lock:
            self.load_model(backend, model_name, device, compute_type, kwargs.get("cancel_callback"))
            assert self._backend is not None
            options = TranscriptionOptions(
                language=str(kwargs.get("language", "auto")),
                beam_size=int(kwargs.get("beam_size") or 5),
                best_of=kwargs.get("best_of") if isinstance(kwargs.get("best_of"), int) else None,
                temperature=float(kwargs.get("temperature") or 0.0),
                initial_prompt=str(kwargs.get("initial_prompt") or ""),
                word_timestamps=bool(kwargs.get("word_timestamps", False)),
                condition_on_previous_text=bool(kwargs.get("condition_on_previous_text", True)),
                no_speech_threshold=kwargs.get("no_speech_threshold") if isinstance(kwargs.get("no_speech_threshold"), (int, float)) else None,
                progress_callback=kwargs.get("progress_callback") if callable(kwargs.get("progress_callback")) else None,
                cancel_callback=kwargs.get("cancel_callback") if callable(kwargs.get("cancel_callback")) else None,
            )
            self._metadata["model_status"] = "running"
            started = monotonic()
            try:
                result = self._backend.transcribe(audio_path, options)
            except BackendOutOfMemoryError:
                self._metadata.update({"model_status": "failed", "failure_stage": "inference"})
                self._release_locked("failed")
                raise
            except torch.cuda.OutOfMemoryError as exc:
                self._metadata.update({"model_status": "failed", "failure_stage": "inference"})
                self._release_locked("failed")
                raise BackendOutOfMemoryError("oom_inference", str(exc), stage="inference") from exc
            except Exception:
                self._metadata["model_status"] = "failed"
                self._release_locked("failed")
                raise
            self._metadata.update({
                "model_status": "ready",
                "inference_duration_seconds": round(monotonic() - started, 6),
            })
            result["runtime_metadata"] = dict(self._metadata)
            return result
