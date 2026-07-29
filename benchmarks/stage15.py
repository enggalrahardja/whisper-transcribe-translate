#!/usr/bin/env python3
"""Collect Stage 15 local benchmark runs into comparison/profile artefacts."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import platform
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
MODELS = {
    "base": "base.pt", "small": "small.pt", "medium": "medium.pt",
    "large-v3": "large-v3.pt", "large-v3-turbo": "large-v3-turbo.pt",
}


def _mean(values: list[float | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return statistics.fmean(usable) if usable else None


def _gpu() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "devices": [], "limitation": "nvidia-smi unavailable"}
    discovered = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    available = result.returncode == 0 and bool(discovered)
    return {"available": available, "devices": discovered if available else [],
            "limitation": None if available else (result.stderr.strip() or result.stdout.strip() or "no accessible NVIDIA GPU")}


def _summarize_run(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("results", [])
    completed = [row for row in rows if row.get("status") == "completed"]
    try:
        result_reference = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        result_reference = str(path)
    return {
        "status": "completed" if completed and len(completed) == len(rows) else "failed",
        "cases": len(rows), "completedCases": len(completed),
        "wer": _mean([row.get("accuracy", {}).get("wer") for row in completed]),
        "cer": _mean([row.get("accuracy", {}).get("cer") for row in completed]),
        "partialLatencyMs": _mean([row.get("latency", {}).get("partial_latency_ms") for row in completed]),
        "stableLatencyMs": _mean([row.get("latency", {}).get("stable_latency_ms") for row in completed]),
        "finalLatencyMs": _mean([row.get("latency", {}).get("final_latency_ms") for row in completed]),
        "realTimeFactor": _mean([row.get("latency", {}).get("real_time_factor") for row in completed]),
        "modelLoadTimeMs": _mean([row.get("latency", {}).get("model_load_time_ms") for row in completed]),
        "cpuPeakPercent": _mean([row.get("resource_usage", {}).get("cpu_percent_peak") for row in completed]),
        "ramPeakMiB": _mean([row.get("resource_usage", {}).get("ram_mib_peak") for row in completed]),
        "gpuPeakPercent": _mean([row.get("resource_usage", {}).get("gpu_percent_peak") for row in completed]),
        "vramPeakMiB": _mean([row.get("resource_usage", {}).get("vram_mib_peak") for row in completed]),
        "failureRate": (len(rows) - len(completed)) / len(rows) if rows else None,
        "resultReference": result_reference,
    }


def collect(output_dir: Path) -> dict[str, Any]:
    manifest = json.loads((ROOT / "dataset/manifest.json").read_text(encoding="utf-8"))
    model_dir = PROJECT_ROOT / "storage/models/whisper"
    comparisons: list[dict[str, Any]] = []
    unsupported: list[dict[str, str]] = []
    for model, checkpoint in MODELS.items():
        available = (model_dir / checkpoint).is_file()
        run = _summarize_run(output_dir / model / "results.json")
        row = {"component": "transcription", "provider": "local-whisper", "model": model,
               "checkpoint": checkpoint, "available": available, **(run or {"status": "not_run"})}
        comparisons.append(row)
        if not available:
            unsupported.append({"component": "transcription", "model": model, "reason": f"checkpoint missing: storage/models/whisper/{checkpoint}"})
        elif run is None:
            unsupported.append({"component": "transcription", "model": model, "reason": "available but benchmark output is absent"})
    components = [
        ("translation", "Helsinki-NLP/opus-mt-id-en", "transformers", "Marian checkpoint/dependency not available locally"),
        ("diarization", "speechbrain/spkrec-ecapa-voxceleb", "speechbrain", "ECAPA checkpoint not pinned in local benchmark cache"),
    ]
    for component, model, dependency, reason in components:
        dependency_available = importlib.util.find_spec(dependency) is not None
        comparisons.append({"component": component, "provider": f"local-{dependency}", "model": model,
                            "checkpoint": "main (exact resolved commit required at run time)",
                            "available": False, "status": "not_run", "dependencyAvailable": dependency_available})
        unsupported.append({"component": component, "model": model, "reason": reason})
    gpu = _gpu()
    payload = {
        "schemaVersion": "1.0", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "benchmarkDate": datetime.now(timezone.utc).date().isoformat(),
        "dataset": {"id": manifest["dataset_id"], "version": manifest["dataset_version"],
                    "cases": len([case for case in manifest["cases"] if case.get("enabled")])},
        "hardware": {"os": platform.platform(), "processor": platform.processor() or None,
                     "logicalCpuCount": os.cpu_count(), "python": platform.python_version(), "gpu": gpu},
        "comparison": comparisons, "unsupported": unsupported,
        "profiles": json.loads((PROJECT_ROOT / "config/model-profiles.json").read_text(encoding="utf-8")),
        "limitations": [
            "Synthetic speech is a reproducibility fixture and does not represent natural microphone accuracy.",
            "Offline Whisper emits final only; partial and stable latency are not applicable.",
            "Model comparison is incomplete until all checkpoints run on this exact dataset and hardware.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fields = sorted({key for row in comparisons for key in row})
    with (output_dir / "comparison.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(comparisons)
    lines = ["# Stage 15 internal benchmark", "", f"Dataset: `{manifest['dataset_id']}` v`{manifest['dataset_version']}`; date: {payload['benchmarkDate']}.", "",
             "| Component | Model | Available | Status | WER | CER | Final ms | RTF | Load ms | Failure rate |",
             "|---|---|---:|---|---:|---:|---:|---:|---:|---:|"]
    def show(value: Any) -> str: return "n/a" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)
    for row in comparisons:
        lines.append("| " + " | ".join(show(row.get(key)) for key in ("component", "model", "available", "status", "wer", "cer", "finalLatencyMs", "realTimeFactor", "modelLoadTimeMs", "failureRate")) + " |")
    lines += ["", "## Unsupported/unexecuted", ""] + [f"- `{item['model']}` ({item['component']}): {item['reason']}" for item in unsupported]
    lines += ["", "## Interpretation", "", "No model is designated as best. Only successful rows are measured; unavailable rows are explicit. Profiles fall back to Fast/base and remain disabled by default.", "", "## Limitations", ""] + [f"- {item}" for item in payload["limitations"]]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    hardware_line = "NVIDIA GPU telemetry: available." if gpu["available"] else f"NVIDIA GPU telemetry unavailable: {str(gpu['limitation']).rstrip('.')}. CPU fallback is required."
    (output_dir / "unsupported.md").write_text(
        "# Unsupported hardware/model report\n\n## Hardware\n\n- " + hardware_line
        + "\n\n## Models and components\n\n"
        + "\n".join(f"- **{item['model']}**: {item['reason']}" for item in unsupported) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results/stage15")
    args = parser.parse_args()
    payload = collect(args.output_dir.resolve())
    print(f"Wrote Stage 15 comparison with {len(payload['comparison'])} model/component rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
