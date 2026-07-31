"""Fail-fast compatibility checks for the worker's native inference stack."""

from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version


PINNED_WORKER_DEPENDENCIES: dict[str, str | None] = {
    "numpy": "1.26.2",
    "numba": "0.58.1",
    "llvmlite": "0.41.1",
    "coverage": None,
    "torch": "2.13.0",
    "torchaudio": "2.11.0",
}
OPTIONAL_BACKEND_DEPENDENCIES = ("faster-whisper", "ctranslate2")

if platform.system() == "Linux" and platform.machine() == "x86_64":
    PINNED_WORKER_DEPENDENCIES["triton"] = "3.7.1"


class WorkerDependencyMismatch(RuntimeError):
    pass


def worker_dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in (*PINNED_WORKER_DEPENDENCIES, *OPTIONAL_BACKEND_DEPENDENCIES):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    return versions


def validate_worker_dependencies() -> dict[str, str | None]:
    installed = worker_dependency_versions()
    mismatches: list[str] = []
    for package, expected in PINNED_WORKER_DEPENDENCIES.items():
        actual = installed[package]
        if expected is None:
            if actual is not None:
                mismatches.append(f"{package} must be absent, found {actual}")
        elif actual != expected:
            mismatches.append(f"{package} expected {expected}, found {actual or 'not installed'}")

    if not mismatches and "triton" in PINNED_WORKER_DEPENDENCIES:
        try:
            from src.whisper.triton_ops import median_kernel

            median_kernel(3)
        except Exception as exc:
            mismatches.append(
                f"Whisper/Triton source mutation preflight failed: {type(exc).__name__}: {exc}"
            )

    if mismatches:
        raise WorkerDependencyMismatch(
            "Worker dependency compatibility check failed: " + "; ".join(mismatches)
        )
    return installed
