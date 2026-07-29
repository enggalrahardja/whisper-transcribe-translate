"""Content-free operational metric aggregation for the local pipeline."""
from __future__ import annotations
import math, os, time
from typing import Iterable

SENSITIVE = {"text", "rawText", "sourceText", "translatedText", "audio", "sessionId", "segmentId", "jobId"}

def percentile(values: Iterable[float], percent: float) -> float:
    ordered = sorted(float(v) for v in values)
    if not ordered: return 0.0
    rank = (len(ordered) - 1) * percent / 100
    low, high = math.floor(rank), math.ceil(rank)
    if low == high: return round(ordered[low], 3)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (rank - low), 3)

def latency_summary(values: Iterable[float]) -> dict[str, float]:
    data = list(values)
    return {"averageMs": round(sum(data) / len(data), 3) if data else 0.0, "p50Ms": percentile(data, 50), "p95Ms": percentile(data, 95), "p99Ms": percentile(data, 99)}

def redact_metrics(value):
    if isinstance(value, dict): return {k: redact_metrics(v) for k, v in value.items() if k not in SENSITIVE and not any(word in k.lower() for word in ("secret", "token", "password", "credential", "transcript"))}
    if isinstance(value, list): return [redact_metrics(v) for v in value]
    return value

def resource_metrics() -> dict[str, float | None]:
    try:
        import psutil
        process = psutil.Process(os.getpid())
        memory = psutil.virtual_memory()
        return {"cpuPercent": psutil.cpu_percent(None), "ramPercent": memory.percent, "processRamMb": round(process.memory_info().rss / 1048576, 2), "gpuUtilizationPercent": None, "vramUsedMb": None, "vramTotalMb": None}
    except Exception:
        return {"cpuPercent": 0.0, "ramPercent": 0.0, "processRamMb": 0.0, "gpuUtilizationPercent": None, "vramUsedMb": None, "vramTotalMb": None}

def warnings_for(workers: dict, *, queue_warning=.8, latency_warning_ms=5000, failure_warning=.1, persistence_degraded=0) -> list[dict[str, str]]:
    warnings=[]
    for name, worker in workers.items():
        capacity=max(1, worker.get("capacity", 0)); depth=worker.get("queueDepth", 0)
        if not worker.get("ready", False): warnings.append({"code":"worker_unavailable","worker":name})
        if depth/capacity >= queue_warning: warnings.append({"code":"queue_utilization","worker":name})
        if worker.get("averageProcessingMs", 0) >= latency_warning_ms: warnings.append({"code":"processing_latency","worker":name})
        total=worker.get("completed",0)+worker.get("failed",0)
        if total and worker.get("failed",0)/total >= failure_warning: warnings.append({"code":"high_failure_rate","worker":name})
    if persistence_degraded: warnings.append({"code":"persistence_degraded","worker":"persistence"})
    return warnings

def quality_indicators(metrics: dict) -> dict[str, float]:
    segments=max(1, metrics.get("segmentCount",0)); chunks=max(1, metrics.get("chunksSent",0))
    return {
        "emptyTranscriptRate": metrics.get("emptyTranscripts",0)/segments,
        "repeatedPhraseRate": metrics.get("duplicatePhrases",0)/segments,
        "lowConfidenceSpeakerRate": metrics.get("lowConfidenceAssignments",0)/segments,
        "untranslatedSegmentRate": metrics.get("untranslatedSegments",0)/segments,
        "translationFallbackRate": metrics.get("translationFallbacks",0)/segments,
        "accurateFinalReplacementRate": metrics.get("finalReplacements",0)/segments,
        "glossaryCorrectionRate": metrics.get("glossaryCorrectedSegments",0)/segments,
        "droppedAudioRate": metrics.get("chunksLost",0)/chunks,
        "processingRealTimeFactor": metrics.get("processingSeconds",0)/max(.001,metrics.get("audioSeconds",0)),
    }
