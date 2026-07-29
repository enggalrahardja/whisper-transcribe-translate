#!/usr/bin/env python3
"""Reproducible, provider-neutral speech benchmark runner (standard library first)."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import queue
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "dataset" / "manifest.json"
REQUIRED_PROFILES = {
    "indonesian",
    "english",
    "code_switching_id_en",
    "technical_meeting",
    "quiet_microphone",
    "far_field_meeting_room",
    "background_noise",
    "multiple_speakers",
    "overlapping_speech",
}
EMPTY_CSV_FIELDS = [
    "case_id", "status", "provider", "model", "model_version", "deployment",
    "hardware.os", "hardware.processor", "hardware.logical_cpu_count", "hardware.gpus",
    "audio_profile", "language", "target_language", "audio_duration_seconds",
    "accuracy.wer", "accuracy.cer",
    "translation_evaluation.source_input", "translation_evaluation.reference_output",
    "translation_evaluation.provider_output", "translation_evaluation.automatic_score",
    "translation_evaluation.human_review_status",
    "latency.partial_latency_ms", "latency.stable_latency_ms", "latency.final_latency_ms",
    "latency.final_latency_origin", "latency.translation_latency_ms", "latency.diarization_latency_ms",
    "latency.model_load_time_ms", "latency.wall_time_seconds", "latency.real_time_factor",
    "resource_usage.cpu_percent_mean", "resource_usage.cpu_percent_peak",
    "resource_usage.ram_mib_mean", "resource_usage.ram_mib_peak",
    "resource_usage.gpu_percent_mean", "resource_usage.gpu_percent_peak",
    "resource_usage.vram_mib_mean", "resource_usage.vram_mib_peak",
    "tested_at", "errors", "limitations",
]


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(character if character.isalnum() or character.isspace() else " " for character in text)
    return " ".join(text.split())


def _edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for ref_index, ref_item in enumerate(reference, start=1):
        current = [ref_index]
        for hyp_index, hyp_item in enumerate(hypothesis, start=1):
            current.append(min(
                current[-1] + 1,
                previous[hyp_index] + 1,
                previous[hyp_index - 1] + (ref_item != hyp_item),
            ))
        previous = current
    return previous[-1]


def error_rate(reference: str, hypothesis: str, *, unit: str) -> float | None:
    normalized_reference = _normalize_text(reference)
    normalized_hypothesis = _normalize_text(hypothesis)
    if unit == "word":
        reference_units = normalized_reference.split()
        hypothesis_units = normalized_hypothesis.split()
    elif unit == "character":
        reference_units = list(normalized_reference.replace(" ", ""))
        hypothesis_units = list(normalized_hypothesis.replace(" ", ""))
    else:
        raise ValueError(f"Unknown error-rate unit: {unit}")
    if not reference_units:
        return 0.0 if not hypothesis_units else None
    return _edit_distance(reference_units, hypothesis_units) / len(reference_units)


def translation_chrf(reference: str, hypothesis: str, *, max_order: int = 6) -> float | None:
    """Deterministic character n-gram F-score (chrF-style, scaled 0..100)."""
    reference = _normalize_text(reference).replace(" ", "")
    hypothesis = _normalize_text(hypothesis).replace(" ", "")
    if not reference:
        return 100.0 if not hypothesis else None
    if not hypothesis:
        return 0.0
    scores: list[float] = []
    for order in range(1, min(max_order, len(reference), len(hypothesis)) + 1):
        ref = [reference[i:i + order] for i in range(len(reference) - order + 1)]
        hyp = [hypothesis[i:i + order] for i in range(len(hypothesis) - order + 1)]
        ref_counts = {item: ref.count(item) for item in set(ref)}
        hyp_counts = {item: hyp.count(item) for item in set(hyp)}
        overlap = sum(min(count, hyp_counts.get(item, 0)) for item, count in ref_counts.items())
        precision = overlap / len(hyp)
        recall = overlap / len(ref)
        scores.append(0.0 if not precision or not recall else 2 * precision * recall / (precision + recall))
    return statistics.fmean(scores) * 100 if scores else 0.0


def _load_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as manifest_file:
        value = json.load(manifest_file)
    if not isinstance(value, dict):
        raise ValueError("Manifest root must be an object")
    return value


def validate_manifest(path: Path, *, require_enabled_files: bool = True) -> list[str]:
    errors: list[str] = []
    try:
        manifest = _load_manifest(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"manifest: {exc}"]
    if manifest.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        return errors + ["cases must be a non-empty array"]
    seen_ids: set[str] = set()
    covered_profiles: set[str] = set()
    base = path.parent.resolve()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", case_id):
            errors.append(f"{prefix}.id must use lowercase letters, digits, underscore, or hyphen")
        elif case_id in seen_ids:
            errors.append(f"{prefix}.id is duplicated: {case_id}")
        else:
            seen_ids.add(case_id)
        profiles = case.get("profiles")
        if not isinstance(profiles, list) or not profiles or not all(isinstance(item, str) for item in profiles):
            errors.append(f"{prefix}.profiles must be a non-empty string array")
        else:
            covered_profiles.update(profiles)
        provenance = case.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("contains_sensitive_data") is not False:
            errors.append(f"{prefix}.provenance must explicitly set contains_sensitive_data to false")
        for field in ("audio_path", "reference_transcript_path"):
            raw = case.get(field)
            if not isinstance(raw, str) or not raw:
                errors.append(f"{prefix}.{field} is required")
                continue
            resolved = (base / raw).resolve()
            if not resolved.is_relative_to(base):
                errors.append(f"{prefix}.{field} escapes the dataset directory")
            elif case.get("enabled") and require_enabled_files and not resolved.is_file():
                errors.append(f"{prefix}.{field} does not exist: {raw}")
        translation_path = case.get("reference_translation_path")
        if translation_path is not None:
            if not isinstance(translation_path, str) or not translation_path:
                errors.append(f"{prefix}.reference_translation_path must be a path or null")
            else:
                resolved = (base / translation_path).resolve()
                if not resolved.is_relative_to(base):
                    errors.append(f"{prefix}.reference_translation_path escapes the dataset directory")
                elif case.get("enabled") and require_enabled_files and not resolved.is_file():
                    errors.append(f"{prefix}.reference_translation_path does not exist: {translation_path}")
        if case.get("enabled"):
            digest = case.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                errors.append(f"{prefix}.sha256 is required for enabled cases")
            consent = provenance.get("consent_or_license") if isinstance(provenance, dict) else None
            if not consent or consent == "required-before-enable":
                errors.append(f"{prefix}.provenance.consent_or_license is required for enabled cases")
    missing_profiles = sorted(REQUIRED_PROFILES - covered_profiles)
    if missing_profiles:
        errors.append("required profiles missing: " + ", ".join(missing_profiles))
    return errors


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _duration_seconds(path: Path) -> float | None:
    if path.suffix.casefold() == ".wav":
        try:
            with wave.open(str(path), "rb") as source:
                return source.getnframes() / source.getframerate()
        except (wave.Error, ZeroDivisionError):
            pass
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        try:
            return float(completed.stdout.strip()) if completed.returncode == 0 else None
        except ValueError:
            return None
    return None


def _query_gpu() -> tuple[list[float], list[float]] | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    completed = subprocess.run(
        [executable, "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        return None
    utilization: list[float] = []
    memory: list[float] = []
    try:
        for line in completed.stdout.splitlines():
            gpu_percent, memory_mib = line.split(",", maxsplit=1)
            utilization.append(float(gpu_percent.strip()))
            memory.append(float(memory_mib.strip()))
    except ValueError:
        return None
    return utilization, memory


class ResourceSampler:
    def __init__(self, pid: int, interval: float = 0.2) -> None:
        self.pid = pid
        self.interval = interval
        self.stop_event = threading.Event()
        self.cpu: list[float] = []
        self.ram_mib: list[float] = []
        self.gpu: list[float] = []
        self.vram_mib: list[float] = []
        self.limitations: list[str] = []
        self.thread = threading.Thread(target=self._run, name="benchmark-resource-sampler", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> dict[str, float | None]:
        self.stop_event.set()
        self.thread.join(timeout=max(2.0, self.interval * 3))
        return {
            "cpu_percent_mean": statistics.fmean(self.cpu) if self.cpu else None,
            "cpu_percent_peak": max(self.cpu) if self.cpu else None,
            "ram_mib_mean": statistics.fmean(self.ram_mib) if self.ram_mib else None,
            "ram_mib_peak": max(self.ram_mib) if self.ram_mib else None,
            "gpu_percent_mean": statistics.fmean(self.gpu) if self.gpu else None,
            "gpu_percent_peak": max(self.gpu) if self.gpu else None,
            "vram_mib_mean": statistics.fmean(self.vram_mib) if self.vram_mib else None,
            "vram_mib_peak": max(self.vram_mib) if self.vram_mib else None,
        }

    def _run(self) -> None:
        try:
            import psutil  # type: ignore
        except ImportError:
            psutil = None
            self.limitations.append("psutil unavailable; process CPU and RAM were not sampled")
        process = None
        previous_cpu_seconds: float | None = None
        previous_cpu_sample = time.perf_counter()
        if psutil is not None:
            try:
                process = psutil.Process(self.pid)
                times = process.cpu_times()
                previous_cpu_seconds = times.user + times.system
            except psutil.Error:
                process = None
        gpu_supported = _query_gpu() is not None
        if not gpu_supported:
            self.limitations.append("nvidia-smi unavailable; GPU and VRAM were not sampled")
        while not self.stop_event.wait(self.interval):
            if process is not None:
                try:
                    processes = [process, *process.children(recursive=True)]
                    sampled_at = time.perf_counter()
                    total_cpu_seconds = sum(item.cpu_times().user + item.cpu_times().system for item in processes)
                    if previous_cpu_seconds is not None:
                        elapsed = sampled_at - previous_cpu_sample
                        self.cpu.append(max(0.0, total_cpu_seconds - previous_cpu_seconds) / elapsed * 100 if elapsed > 0 else 0.0)
                    previous_cpu_seconds = total_cpu_seconds
                    previous_cpu_sample = sampled_at
                    self.ram_mib.append(sum(item.memory_info().rss for item in processes) / (1024 * 1024))
                except psutil.Error:
                    process = None
            if gpu_supported:
                sample = _query_gpu()
                if sample is not None:
                    utilization, memory = sample
                    self.gpu.append(max(utilization, default=0.0))
                    self.vram_mib.append(sum(memory))


def _hardware() -> dict[str, Any]:
    gpu = _query_gpu()
    gpu_names: list[str] = []
    executable = shutil.which("nvidia-smi")
    if executable:
        completed = subprocess.run(
            [executable, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if completed.returncode == 0:
            gpu_names = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "logical_cpu_count": os.cpu_count(),
        "gpus": gpu_names,
        "gpu_sampler_available": gpu is not None,
    }


def _read_reference(base: Path, relative_path: str | None) -> str | None:
    return None if relative_path is None else (base / relative_path).read_text(encoding="utf-8").strip()


def _command_argv(command: str) -> list[str]:
    values = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        values = [value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value for value in values]
    if not values:
        raise ValueError("provider command is empty")
    return values


def _run_case(case: dict[str, Any], manifest_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    base = manifest_path.parent.resolve()
    audio_path = (base / case["audio_path"]).resolve()
    expected_digest = case["sha256"]
    actual_digest = _sha256(audio_path)
    if actual_digest != expected_digest:
        raise ValueError(f"SHA-256 mismatch for {case['id']}: expected {expected_digest}, got {actual_digest}")
    duration = _duration_seconds(audio_path)
    reference = _read_reference(base, case["reference_transcript_path"]) or ""
    reference_translation = _read_reference(base, case.get("reference_translation_path"))
    environment = os.environ.copy()
    environment.update({
        "BENCHMARK_AUDIO": str(audio_path),
        "BENCHMARK_LANGUAGE": str(case.get("language") or "auto"),
        "BENCHMARK_TARGET_LANGUAGE": str(case.get("target_language") or ""),
        "BENCHMARK_MODEL": args.model,
        "BENCHMARK_BEAM_SIZE": str(getattr(args, "beam_size", 5)),
        "BENCHMARK_CASE_ID": case["id"],
    })
    started_at = datetime.now(timezone.utc)
    start = time.perf_counter()
    process = subprocess.Popen(
        _command_argv(args.provider_command),
        cwd=str(Path.cwd()),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    sampler = ResourceSampler(process.pid, args.sample_interval)
    sampler.start()
    events: dict[str, dict[str, Any]] = {}
    parse_errors: list[str] = []
    output_queue: queue.Queue[tuple[int, str] | None] = queue.Queue()
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        assert process.stdout is not None
        for line_number, line in enumerate(process.stdout, start=1):
            output_queue.put((line_number, line))
        output_queue.put(None)

    def read_stderr() -> None:
        assert process.stderr is not None
        stderr_lines.extend(process.stderr)

    stdout_thread = threading.Thread(target=read_stdout, name="benchmark-provider-stdout", daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, name="benchmark-provider-stderr", daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    deadline = start + args.timeout_seconds
    stdout_complete = False
    try:
        while not stdout_complete:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                process.kill()
                parse_errors.append(f"provider timed out after {args.timeout_seconds:g} seconds")
                break
            try:
                queued = output_queue.get(timeout=min(0.1, remaining))
            except queue.Empty:
                if process.poll() is not None and not stdout_thread.is_alive():
                    break
                continue
            if queued is None:
                stdout_complete = True
                continue
            line_number, line = queued
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                event_type = event.get("event")
                if event_type not in {"partial", "stable", "translation", "diarization", "model_loaded", "audio_end", "final"}:
                    raise ValueError("unsupported event")
                if event_type not in events:
                    events[event_type] = {**event, "observed_seconds": time.perf_counter() - start}
            except (json.JSONDecodeError, ValueError, AttributeError) as exc:
                parse_errors.append(f"stdout line {line_number}: {exc}: {line[:200]}")
        remaining = max(0.0, deadline - time.perf_counter())
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait()
            if not any("timed out" in error for error in parse_errors):
                parse_errors.append(f"provider timed out after {args.timeout_seconds:g} seconds")
    finally:
        resources = sampler.stop()
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    wall_seconds = time.perf_counter() - start
    stderr = "".join(stderr_lines).strip()
    final_event = events.get("final", {})
    hypothesis = str(final_event.get("text") or "").strip()
    translation_hypothesis = str(final_event.get("translation") or events.get("translation", {}).get("text") or "").strip() or None
    endpoint = events.get("audio_end", {}).get("observed_seconds")
    final_observed = final_event.get("observed_seconds")
    final_latency = None
    latency_origin = None
    if final_observed is not None:
        if endpoint is not None:
            final_latency = max(0.0, final_observed - endpoint) * 1000
            latency_origin = "audio_end_event"
        else:
            final_latency = final_observed * 1000
            latency_origin = "provider_start_no_audio_end_event"
    limitations = [*sampler.limitations]
    if endpoint is None:
        limitations.append("provider emitted no audio_end event; final latency is measured from provider start")
    if "partial" not in events:
        limitations.append("provider emitted no partial event")
    if "stable" not in events:
        limitations.append("provider emitted no stable event")
    errors = parse_errors[:]
    if return_code != 0:
        errors.append(f"provider exited with code {return_code}: {stderr[-1000:]}")
    if not hypothesis:
        errors.append("provider emitted no non-empty final transcript")
    return {
        "case_id": case["id"],
        "status": "failed" if errors else "completed",
        "provider": args.provider,
        "model": args.model,
        "model_version": args.model_version,
        "model_metadata": {
            "checkpoint": events.get("model_loaded", {}).get("checkpoint"),
            "checkpoint_sha256": events.get("model_loaded", {}).get("checkpoint_sha256"),
            "device": events.get("model_loaded", {}).get("device"),
            "compute_type": events.get("model_loaded", {}).get("compute_type"),
            "beam_size": events.get("model_loaded", {}).get("beam_size", getattr(args, "beam_size", 5)),
        },
        "deployment": args.deployment,
        "hardware": _hardware(),
        "audio_profile": case["profiles"],
        "language": case.get("language"),
        "target_language": case.get("target_language"),
        "audio_duration_seconds": duration,
        "accuracy": {
            "wer": error_rate(reference, hypothesis, unit="word") if hypothesis else None,
            "cer": error_rate(reference, hypothesis, unit="character") if hypothesis else None,
        },
        "translation_evaluation": {
            "source_input": hypothesis or None,
            "reference_output": reference_translation,
            "provider_output": translation_hypothesis,
            "automatic_score": translation_chrf(reference_translation, translation_hypothesis) if reference_translation is not None and translation_hypothesis is not None else None,
            "automatic_metric": "chrF-style-character-F1" if reference_translation is not None else None,
            "human_review_status": "pending" if reference_translation is not None else "not_applicable",
        },
        "latency": {
            "partial_latency_ms": events.get("partial", {}).get("observed_seconds", None) * 1000 if "partial" in events else None,
            "stable_latency_ms": events.get("stable", {}).get("observed_seconds", None) * 1000 if "stable" in events else None,
            "final_latency_ms": final_latency,
            "final_latency_origin": latency_origin,
            "translation_latency_ms": events.get("translation", {}).get("observed_seconds", None) * 1000 if "translation" in events else None,
            "diarization_latency_ms": events.get("diarization", {}).get("observed_seconds", None) * 1000 if "diarization" in events else None,
            "model_load_time_ms": events.get("model_loaded", {}).get("latency_ms"),
            "wall_time_seconds": wall_seconds,
            "real_time_factor": wall_seconds / duration if duration and duration > 0 else None,
        },
        "resource_usage": resources,
        "tested_at": started_at.isoformat(),
        "errors": errors,
        "limitations": limitations,
    }


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _flatten(record: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    def visit(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(f"{prefix}.{key}" if prefix else key, child)
        elif isinstance(value, list):
            output[prefix] = json.dumps(value, ensure_ascii=False)
        else:
            output[prefix] = value
    visit("", record)
    return output


def _report(payload: dict[str, Any]) -> str:
    records = payload["results"]
    lines = [
        "# Benchmark report",
        "",
        f"- Run ID: `{payload['run_id']}`",
        f"- Dataset: `{payload['dataset']['id']}` version `{payload['dataset']['version']}`",
        f"- Provider/model: `{payload['configuration']['provider']}` / `{payload['configuration']['model']}`",
        f"- Deployment: `{payload['configuration']['deployment']}`",
        f"- Started: {payload['started_at']}",
        f"- Cases: {len(records)}",
        "",
        "| Case | Status | WER | CER | Partial ms | Stable ms | Final ms | Load ms | RTF | CPU peak % | RAM peak MiB | GPU peak % | VRAM peak MiB |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    def display(value: Any) -> str:
        return "n/a" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)
    for record in records:
        lines.append("| " + " | ".join([
            record["case_id"], record["status"], display(record["accuracy"]["wer"]), display(record["accuracy"]["cer"]),
            display(record["latency"]["partial_latency_ms"]), display(record["latency"]["stable_latency_ms"]),
            display(record["latency"]["final_latency_ms"]), display(record["latency"].get("model_load_time_ms")), display(record["latency"]["real_time_factor"]),
            display(record["resource_usage"]["cpu_percent_peak"]), display(record["resource_usage"]["ram_mib_peak"]),
            display(record["resource_usage"]["gpu_percent_peak"]), display(record["resource_usage"]["vram_mib_peak"]),
        ]) + " |")
    if not records:
        lines.extend(["", "No enabled, validated dataset cases were available; no benchmark values were fabricated."])
    limitations = sorted({
        *payload.get("run_limitations", []),
        *(item for record in records for item in record["limitations"]),
    })
    errors = [f"{record['case_id']}: {item}" for record in records for item in record["errors"]]
    lines.extend(["", "## Limitations", ""] + ([f"- {item}" for item in limitations] if limitations else ["- None reported."]))
    lines.extend(["", "## Errors", ""] + ([f"- {item}" for item in errors] if errors else ["- None reported."]))
    return "\n".join(lines) + "\n"


def _write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    _atomic_text(output_dir / "results.json", json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    flat = [_flatten(record) for record in payload["results"]]
    fieldnames = sorted({key for row in flat for key in row}) if flat else EMPTY_CSV_FIELDS
    csv_buffer: list[str] = []
    if fieldnames:
        import io
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat)
        csv_buffer.append(stream.getvalue())
    _atomic_text(output_dir / "results.csv", "".join(csv_buffer))
    _atomic_text(output_dir / "report.md", _report(payload))


def command_validate(args: argparse.Namespace) -> int:
    errors = validate_manifest(args.manifest, require_enabled_files=True)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    manifest = _load_manifest(args.manifest)
    enabled = sum(bool(case.get("enabled")) for case in manifest["cases"])
    print(f"Manifest valid: {len(manifest['cases'])} cases, {enabled} enabled")
    return 0


def command_run(args: argparse.Namespace) -> int:
    errors = validate_manifest(args.manifest, require_enabled_files=True)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    manifest = _load_manifest(args.manifest)
    cases = [case for case in manifest["cases"] if case.get("enabled")]
    if args.case:
        cases = [case for case in cases if case["id"] in set(args.case)]
    started = datetime.now(timezone.utc)
    try:
        manifest_display = str(args.manifest.relative_to(Path.cwd().resolve()))
    except ValueError:
        manifest_display = str(args.manifest)
    results: list[dict[str, Any]] = []
    for case in cases:
        print(f"Running {case['id']}...", file=sys.stderr)
        try:
            results.append(_run_case(case, args.manifest, args))
        except Exception as exc:
            results.append({
                "case_id": case["id"], "status": "failed", "provider": args.provider,
                "model": args.model, "model_version": args.model_version, "deployment": args.deployment,
                "model_metadata": {"checkpoint": None, "checkpoint_sha256": None, "device": None, "compute_type": None, "beam_size": getattr(args, "beam_size", 5)},
                "hardware": _hardware(), "audio_profile": case.get("profiles", []),
                "language": case.get("language"), "target_language": case.get("target_language"),
                "audio_duration_seconds": None, "accuracy": {"wer": None, "cer": None},
                "translation_evaluation": {"source_input": None, "reference_output": None, "provider_output": None, "automatic_score": None, "automatic_metric": None, "human_review_status": "not_run"},
                "latency": {"partial_latency_ms": None, "stable_latency_ms": None, "final_latency_ms": None, "final_latency_origin": None, "translation_latency_ms": None, "diarization_latency_ms": None, "model_load_time_ms": None, "wall_time_seconds": None, "real_time_factor": None},
                "resource_usage": {key: None for key in ("cpu_percent_mean", "cpu_percent_peak", "ram_mib_mean", "ram_mib_peak", "gpu_percent_mean", "gpu_percent_peak", "vram_mib_mean", "vram_mib_peak")},
                "tested_at": datetime.now(timezone.utc).isoformat(), "errors": [f"{type(exc).__name__}: {exc}"], "limitations": [],
            })
    payload = {
        "schema_version": "1.0",
        "run_id": started.strftime("%Y%m%dT%H%M%SZ"),
        "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {"id": manifest.get("dataset_id"), "version": manifest.get("dataset_version"), "manifest": manifest_display},
        "configuration": {"provider": args.provider, "model": args.model, "model_version": args.model_version, "deployment": args.deployment, "beam_size": args.beam_size, "provider_command": args.provider_command},
        "execution": {"isolation": "one_provider_process_per_case", "cold_start_included": True, "sample_interval_seconds": args.sample_interval, "timeout_seconds": args.timeout_seconds},
        "results": results,
        "run_limitations": ["No enabled dataset cases; populate reviewed non-sensitive audio and references before measuring."] if not cases else [],
    }
    _write_outputs(payload, args.output_dir)
    print(f"Wrote {len(results)} result(s) to {args.output_dir}")
    return 1 if any(record["status"] == "failed" for record in results) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate the dataset manifest and enabled files")
    validate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    validate.set_defaults(handler=command_validate)
    run = subparsers.add_parser("run", help="run enabled cases and write JSON, CSV, and Markdown")
    run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    run.add_argument("--provider", required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--model-version", required=True)
    run.add_argument("--deployment", choices=("local", "cloud"), required=True)
    run.add_argument("--provider-command", required=True, help="trusted command that emits provider JSONL events")
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--case", action="append", help="run only this enabled case (repeatable)")
    run.add_argument("--timeout-seconds", type=float, default=1800.0)
    run.add_argument("--sample-interval", type=float, default=0.2)
    run.add_argument("--beam-size", type=int, default=5)
    run.set_defaults(handler=command_run)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "manifest"):
        args.manifest = args.manifest.resolve()
    if hasattr(args, "output_dir"):
        args.output_dir = args.output_dir.resolve()
    if getattr(args, "timeout_seconds", 1) <= 0 or getattr(args, "sample_interval", 1) <= 0 or getattr(args, "beam_size", 1) <= 0:
        parser.error("timeout and sample interval must be positive")
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
