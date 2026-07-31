"""Lazy CUDA 12 runtime discovery for CTranslate2 on Linux."""

from __future__ import annotations

import ctypes
import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping


@dataclass(frozen=True)
class Cuda12RuntimeFailure(RuntimeError):
    detail: str
    message: str
    missing_library: str | None
    remediation: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class Cuda12Runtime:
    library_paths: tuple[str, ...]
    cuda_device_count: int
    supported_compute_types: tuple[str, ...]


def merge_library_path(existing: str | None, additions: tuple[str, ...]) -> str:
    merged: list[str] = []
    for value in (*additions, *((existing or "").split(os.pathsep))):
        if value and value not in merged:
            merged.append(value)
    return os.pathsep.join(merged)


def resolve_cuda12_library_paths() -> tuple[str, ...]:
    packages = (
        ("nvidia.cublas.lib", "libcublas.so.12", "missing_cublas_cuda12", "nvidia-cublas-cu12"),
        ("nvidia.cudnn.lib", "libcudnn.so.9", "missing_cudnn9", "nvidia-cudnn-cu12==9.*"),
    )
    paths: list[str] = []
    for module_name, library, detail, dependency in packages:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise Cuda12RuntimeFailure(
                detail,
                f"{library} is unavailable because Python package {dependency} is not installed",
                library,
                f"Install project dependencies so {dependency} is available, then restart the worker",
            ) from exc
        module_file = getattr(module, "__file__", None)
        candidates = [Path(module_file).resolve().parent] if module_file else []
        candidates.extend(Path(value).resolve() for value in getattr(module, "__path__", ()))
        library_path = next((path for path in candidates if (path / library).exists()), None)
        if library_path is None:
            raise Cuda12RuntimeFailure(
                detail,
                f"{library} was not found in Python package {dependency}",
                library,
                f"Reinstall {dependency}, then restart the worker",
            )
        value = str(library_path)
        if value not in paths:
            paths.append(value)
    return tuple(paths)


def _preload_cuda12_libraries(library_paths: tuple[str, ...]) -> None:
    cublas_path, cudnn_path = (Path(value) for value in library_paths)
    candidates = [
        cublas_path / "libcublasLt.so.12",
        cublas_path / "libcublas.so.12",
        cudnn_path / "libcudnn.so.9",
        *sorted(path for path in cudnn_path.glob("libcudnn_*.so.9")),
    ]
    for library in candidates:
        if not library.exists():
            continue
        try:
            ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            name = library.name
            detail = "missing_cublas_cuda12" if "cublas" in name else "missing_cudnn9"
            raise Cuda12RuntimeFailure(
                detail,
                f"{name} could not be loaded: {exc}",
                name,
                "Reinstall the CUDA 12 Python runtime dependencies, then restart the worker",
            ) from exc

    for name, detail in (("libcublas.so.12", "missing_cublas_cuda12"), ("libcudnn.so.9", "missing_cudnn9")):
        try:
            ctypes.CDLL(name, mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            raise Cuda12RuntimeFailure(
                detail,
                f"{name} is not visible to the worker runtime: {exc}",
                name,
                "Restart the worker through the project start command after installing dependencies",
            ) from exc


def activate_faster_whisper_cuda(
    compute_type: str,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> Cuda12Runtime | None:
    if not sys.platform.startswith("linux"):
        return None

    target_environ = os.environ if environ is None else environ
    library_paths = resolve_cuda12_library_paths()
    target_environ["LD_LIBRARY_PATH"] = merge_library_path(
        target_environ.get("LD_LIBRARY_PATH"), library_paths
    )
    _preload_cuda12_libraries(library_paths)

    try:
        import ctranslate2

        device_count = int(ctranslate2.get_cuda_device_count())
        supported = tuple(sorted(ctranslate2.get_supported_compute_types("cuda")))
    except Exception as exc:
        raise Cuda12RuntimeFailure(
            "cuda_device_unavailable",
            f"CTranslate2 CUDA runtime check failed: {exc}",
            None,
            "Check the NVIDIA driver and restart the worker",
        ) from exc
    if device_count < 1:
        raise Cuda12RuntimeFailure(
            "cuda_device_unavailable",
            "CTranslate2 did not detect a CUDA device",
            None,
            "Check NVIDIA_VISIBLE_DEVICES/CUDA_VISIBLE_DEVICES and the NVIDIA driver, or select CPU",
        )
    if compute_type not in supported:
        raise Cuda12RuntimeFailure(
            "unsupported_compute_type",
            f"CTranslate2 does not support compute type {compute_type} on CUDA",
            None,
            f"Select one of: {', '.join(supported)}",
        )
    return Cuda12Runtime(library_paths, device_count, supported)
