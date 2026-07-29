#!/usr/bin/env python3
"""Reproducible Stage 18 acceptance orchestrator and evidence report writer."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "acceptance/stage18/results"


def sanitize(value: str) -> str:
    result = value.replace(str(ROOT), ".")
    result = result.replace(str(Path.home()), "<HOME>")
    return result


def run_check(name: str, command: list[str], *, cwd: Path = ROOT, timeout: int = 300) -> dict[str, object]:
    started = perf_counter()
    try:
        process = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, shell=False,
        )
        output = "\n".join(part for part in (process.stdout, process.stderr) if part).strip()
        return {
            "name": name, "status": "pass" if process.returncode == 0 else "fail",
            "returnCode": process.returncode,
            "durationSeconds": round(perf_counter() - started, 3),
            "command": sanitize(subprocess.list2cmdline(command)),
            "outputTail": sanitize(output[-4000:]),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name, "status": "fail", "returnCode": None,
            "durationSeconds": round(perf_counter() - started, 3),
            "command": sanitize(subprocess.list2cmdline(command)),
            "outputTail": f"Timed out after {exc.timeout} seconds",
        }
    except OSError as exc:
        return {
            "name": name, "status": "fail", "returnCode": None,
            "durationSeconds": round(perf_counter() - started, 3),
            "command": sanitize(subprocess.list2cmdline(command)), "outputTail": sanitize(str(exc)),
        }


def manual_status(env_name: str, reason: str) -> dict[str, object]:
    value = os.environ.get(env_name, "").strip().lower()
    if value == "pass":
        return {"status": "pass", "evidence": f"Operator supplied {env_name}=pass"}
    if value == "fail":
        return {"status": "fail", "evidence": f"Operator supplied {env_name}=fail"}
    return {"status": "pending", "evidence": reason}


def aggregate_benchmark(path: Path) -> tuple[dict[str, object], dict[str, str]]:
    if not path.is_file():
        return {}, {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    completed = [item for item in payload.get("results", []) if item.get("status") == "completed"]
    mean = lambda values: statistics.fmean(values) if values else None
    values = lambda accessor: [value for item in completed if (value := accessor(item)) is not None]
    performance = {
        "cases": len(payload.get("results", [])), "completedCases": len(completed),
        "wer": mean(values(lambda item: item.get("accuracy", {}).get("wer"))),
        "cer": mean(values(lambda item: item.get("accuracy", {}).get("cer"))),
        "partialLatencyMs": mean(values(lambda item: item.get("latency", {}).get("partial_latency_ms"))),
        "stableLatencyMs": mean(values(lambda item: item.get("latency", {}).get("stable_latency_ms"))),
        "finalLatencyMs": mean(values(lambda item: item.get("latency", {}).get("final_latency_ms"))),
        "accurateFinalLatencyMs": None, "translationLatencyMs": None,
        "diarizationLatencyMs": None, "endToEndLatencyMs": None,
        "realTimeFactor": mean(values(lambda item: item.get("latency", {}).get("real_time_factor"))),
        "cpuPeakPercent": max(values(lambda item: item.get("resource_usage", {}).get("cpu_percent_peak")), default=None),
        "ramPeakMiB": max(values(lambda item: item.get("resource_usage", {}).get("ram_mib_peak")), default=None),
        "gpuPeakPercent": max(values(lambda item: item.get("resource_usage", {}).get("gpu_percent_peak")), default=None),
        "vramPeakMiB": max(values(lambda item: item.get("resource_usage", {}).get("vram_mib_peak")), default=None),
        "droppedChunkRate": None, "duplicateRate": None, "failureFallbackRate": None,
        "queueDepth": None, "estimatedOpenAICost": None,
    }
    scenarios: dict[str, str] = {}
    for item in completed:
        for profile in item.get("audio_profile", []):
            scenarios[profile] = "pass"
    return performance, scenarios


def write_reports(payload: dict[str, object]) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "acceptance.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    matrix = payload["matrix"]
    matrix_lines = ["# Stage 18 pass/fail matrix", "", "| Area | Status | Evidence |", "|---|---|---|"]
    matrix_lines += [f"| {item['area']} | **{str(item['status']).upper()}** | {item['evidence']} |" for item in matrix]
    (OUTPUT / "pass-fail-matrix.md").write_text("\n".join(matrix_lines) + "\n", encoding="utf-8")

    performance = payload["performance"]
    perf_lines = ["# Stage 18 performance report", "", "Values are from the current Stage 18 local `base` benchmark when available.", "", "| Metric | Value |", "|---|---:|"]
    perf_lines += [f"| {key} | {value if value is not None else 'not measured'} |" for key, value in performance.items()]
    perf_lines += ["", "Partial/stable and downstream end-to-end timings are not inferred from unit tests. Missing values remain `not measured`."]
    (OUTPUT / "performance-report.md").write_text("\n".join(perf_lines) + "\n", encoding="utf-8")

    security_lines = ["# Stage 18 security checklist", "", "| Control | Status | Evidence |", "|---|---|---|"]
    security_lines += [f"| {item['control']} | **{str(item['status']).upper()}** | {item['evidence']} |" for item in payload["securityChecklist"]]
    (OUTPUT / "security-checklist.md").write_text("\n".join(security_lines) + "\n", encoding="utf-8")
    limitations = ["# Stage 18 known limitations", ""] + [f"- {item}" for item in payload["knownLimitations"]]
    (OUTPUT / "known-limitations.md").write_text("\n".join(limitations) + "\n", encoding="utf-8")

    report = [
        "# Stage 18 end-to-end acceptance", "",
        f"Generated: `{payload['generatedAt']}`", "",
        f"## Verdict: {payload['verdict']}", "",
        str(payload["verdictReason"]), "",
        "## Evidence summary", "",
        f"- Automated checks: {sum(item['status'] == 'pass' for item in payload['checks'])}/{len(payload['checks'])} passed.",
        f"- Local benchmark: {performance.get('completedCases', 0)}/{performance.get('cases', 0)} cases completed.",
        f"- OpenAI acceptance: {payload['openai']['status']} — {payload['openai']['reason']}",
        "- Detailed matrix, performance, security, and limitations are adjacent machine/generated artifacts.", "",
        "## Release profiles", "",
        "- `development-local` is the default and requires no cloud credentials.",
        "- `production-local` requires production security configuration and local checkpoints, but no cloud credentials.",
        "- `production-hybrid` explicitly selects OpenAI and requires server-side credentials plus external-audio consent.", "",
        "No production-ready claim is made while physical microphone, real process restart/Mongo restore, or deployment security evidence remains pending.",
    ]
    (OUTPUT / "acceptance-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local-benchmark", action="store_true")
    parser.add_argument("--skip-web-build", action="store_true")
    args = parser.parse_args()
    python = sys.executable
    checks = [
        run_check("api-tests", [python, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=ROOT / "services/api"),
        run_check("benchmark-tests", [python, "-m", "unittest", "discover", "-s", "benchmarks/tests", "-q"]),
        run_check("benchmark-manifest", [python, "benchmarks/run.py", "validate"]),
        run_check("python-compile", [python, "-m", "compileall", "-q", "services/api/app", "services/api/tests", "benchmarks", "acceptance/stage18"]),
    ]
    if not args.skip_web_build:
        pnpm = shutil.which("pnpm") or shutil.which("pnpm.cmd") or "pnpm"
        checks.append(run_check("web-build", [pnpm, "--dir", "apps/web", "build"], timeout=300))

    benchmark_path = OUTPUT / "local-base/results.json"
    checkpoint = ROOT / "storage/models/whisper/base.pt"
    if args.run_local_benchmark and checkpoint.is_file():
        try:
            provider_python = str(Path(python).resolve().relative_to(ROOT))
        except ValueError:
            provider_python = python
        checks.append(run_check("local-base-benchmark", [
            python, "benchmarks/run.py", "run", "--provider", "local-whisper",
            "--model", "base", "--model-version", "base.pt", "--deployment", "local",
            "--provider-command", f'"{provider_python}" benchmarks/providers/current_whisper.py',
            "--beam-size", "5", "--timeout-seconds", "600", "--output-dir", str(OUTPUT / "local-base"),
        ], timeout=900))
    elif not benchmark_path.is_file():
        source = ROOT / "benchmarks/results/stage15/base/results.json"
        benchmark_path = source if source.is_file() else benchmark_path

    performance, scenario_results = aggregate_benchmark(benchmark_path)
    automated = all(item["status"] == "pass" for item in checks)
    microphone = manual_status("STAGE18_MICROPHONE_E2E_RESULT", "Physical microphone/device E2E was not available to the headless runner")
    restart = manual_status("STAGE18_API_RESTART_RESTORE_RESULT", "Repository restore tests pass, but API process restart against deployment Mongo/storage was not executed")
    deployment_security = manual_status("STAGE18_DEPLOYMENT_SECURITY_RESULT", "Automated auth/redaction tests pass; deployed TLS/origin/secret-manager acceptance was not executed")
    openai_ready = all(os.environ.get(name) for name in ("OPENAI_API_KEY", "OPENAI_BILLING_APPROVED", "BENCHMARK_CLOUD_DATA_APPROVED"))
    openai = {
        "status": "not_run" if not openai_ready else "pending",
        "reason": "key, billing approval, and cloud-dataset consent were not all available" if not openai_ready else "gates present; paid run requires explicit operator invocation",
    }
    scenario_map = {
        "Bahasa Indonesia": scenario_results.get("indonesian"),
        "Bahasa Inggris": scenario_results.get("english"),
        "Code-switching": scenario_results.get("code_switching_id_en"),
        "Technical terminology": "pass" if automated else "fail",
        "Quiet microphone fixture": scenario_results.get("quiet_microphone"),
        "Background noise fixture": scenario_results.get("background_noise"),
        "Far-field fixture": scenario_results.get("far_field_meeting_room"),
        "Multi-speaker ASR fixture": scenario_results.get("multiple_speakers"),
        "Overlapping-speech ASR fixture": scenario_results.get("overlapping_speech"),
    }
    matrix = [
        {"area": name, "status": status or "pending", "evidence": "Stage 18/15 non-sensitive local base benchmark" if status else "No completed benchmark evidence"}
        for name, status in scenario_map.items()
    ]
    matrix += [
        {"area": "PCM/VAD/state/accurate-final/glossary/downstream queues", "status": "pass" if automated else "fail", "evidence": "API acceptance and regression suite"},
        {"area": "Reconnect and sequence anomaly handling", "status": "pass" if automated else "fail", "evidence": "PCM/VAD/session isolation tests"},
        {"area": "Queue pressure and worker isolation", "status": "pass" if automated else "fail", "evidence": "bounded queue/backpressure/failure-isolation tests"},
        {"area": "Persistence degradation and runtime restore", "status": "pass" if automated else "fail", "evidence": "repository/write-behind/reconnect restore tests"},
        {"area": "Local translation real-model E2E", "status": "pending", "evidence": "Marian checkpoint was unavailable; lifecycle uses deterministic/fake-provider acceptance tests"},
        {"area": "Local diarization real-model E2E", "status": "pending", "evidence": "SpeechBrain checkpoint was not pinned/available; clustering lifecycle tests pass"},
        {"area": "Physical microphone E2E", **microphone},
        {"area": "API restart with deployed persistence", **restart},
        {"area": "Production deployment security", **deployment_security},
        {"area": "OpenAI optional provider", "status": openai["status"], "evidence": openai["reason"]},
    ]
    critical_pending = any(item["status"] != "pass" for item in (microphone, restart, deployment_security))
    real_model_pending = performance.get("translationLatencyMs") is None or performance.get("diarizationLatencyMs") is None
    verdict = "NO-GO" if not automated or critical_pending or real_model_pending else "GO"
    reason = (
        "Production release is blocked by missing physical microphone, deployed restart/restore, deployment security, and real-model translation/diarization acceptance evidence."
        if critical_pending or real_model_pending else "All automated and required manual acceptance gates passed."
    )
    payload = {
        "schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict, "verdictReason": reason,
        "hardware": {"os": platform.platform(), "processor": platform.processor(), "logicalCpuCount": os.cpu_count(), "nvidiaSmiAvailable": shutil.which("nvidia-smi") is not None},
        "checks": checks, "matrix": matrix, "performance": performance,
        "openai": openai,
        "releaseProfiles": json.loads((ROOT / "config/release-profiles.json").read_text(encoding="utf-8")),
        "securityChecklist": [
            {"control": "Authentication/authorization/session ownership", "status": "pass" if automated else "fail", "evidence": "Stage 16 regression tests"},
            {"control": "WebSocket auth/origin/input/rate limits", "status": "pass" if automated else "fail", "evidence": "Stage 16 regression tests"},
            {"control": "Monitoring content/secret redaction", "status": "pass" if automated else "fail", "evidence": "monitoring and persistence redaction tests"},
            {"control": "Local mode makes no cloud request", "status": "pass" if automated else "fail", "evidence": "local defaults/provider-selection tests"},
            {"control": "OpenAI requires explicit key and consent", "status": "pass" if automated else "fail", "evidence": "provider/profile startup tests"},
            {"control": "Deployed TLS/origin/secret manager", **deployment_security},
        ],
        "knownLimitations": [
            "Physical browser microphone E2E was not executed in this headless environment unless explicit operator evidence says otherwise.",
            "API process restart against a real MongoDB and storage deployment was not executed; repository-level restore is automated.",
            "Translation and SpeechBrain diarization checkpoints were unavailable in the Stage 15 hardware snapshot, so their real-model E2E quality/latency remains unmeasured.",
            "Whisper base partial/stable latency is unavailable because the existing model is segment-based rather than true token streaming.",
            "GPU/VRAM values remain unavailable when NVIDIA telemetry or supported hardware is absent.",
            "OpenAI was not called without all credential, billing, and dataset-consent gates.",
            "In-process queues and provider rate limits remain non-durable and per-process.",
        ],
    }
    write_reports(payload)
    print(f"Stage 18 verdict: {verdict}")
    print(f"Evidence: {OUTPUT / 'acceptance.json'}")
    return 0 if automated else 1


if __name__ == "__main__":
    raise SystemExit(main())
