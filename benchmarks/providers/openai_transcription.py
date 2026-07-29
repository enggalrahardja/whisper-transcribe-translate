#!/usr/bin/env python3
"""Opt-in OpenAI adapter for the Stage 1/15 benchmark protocol.

This adapter intentionally refuses to run unless credentials, billing approval,
and dataset cloud-transfer approval are all explicit.
"""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
from pathlib import Path
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def emit(event: str, **values: object) -> None:
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def approved(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def estimate_cost(model: str, usage: object) -> dict[str, object] | None:
    if not isinstance(usage, dict):
        return None
    catalogue = json.loads((PROJECT_ROOT / "config/openai-pricing.json").read_text(encoding="utf-8"))
    entry = next((item for item in catalogue["entries"] if item["model"] == model), None)
    if entry is None or entry["billingUnit"] != "million_audio_tokens":
        return None
    input_tokens = float(usage.get("input_tokens", usage.get("inputTokens", 0)) or 0)
    output_tokens = float(usage.get("output_tokens", usage.get("outputTokens", 0)) or 0)
    amount = input_tokens / 1_000_000 * float(entry["inputPrice"])
    amount += output_tokens / 1_000_000 * float(entry.get("outputPrice") or 0)
    return {
        "amount": round(amount, 8), "currency": catalogue.get("currency", "USD"),
        "pricingCheckedDate": catalogue["checkedDate"], "pricingSource": entry["source"],
    }


def main() -> int:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("SKIPPED: OPENAI_API_KEY is unavailable", file=sys.stderr)
        return 78
    if not approved("OPENAI_BILLING_APPROVED"):
        print("SKIPPED: OPENAI_BILLING_APPROVED is not true", file=sys.stderr)
        return 78
    if not approved("BENCHMARK_CLOUD_DATA_APPROVED"):
        print("SKIPPED: BENCHMARK_CLOUD_DATA_APPROVED is not true", file=sys.stderr)
        return 78

    audio = Path(os.environ["BENCHMARK_AUDIO"])
    model = os.environ.get("BENCHMARK_MODEL", "gpt-4o-transcribe")
    language = os.environ.get("BENCHMARK_LANGUAGE", "auto")
    timeout = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "30"))
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    data = {"model": model, "response_format": "json"}
    if language not in {"auto", "id-en"}:
        data["language"] = language

    emit("audio_end")
    started = perf_counter()
    boundary = f"----benchmark-{uuid4().hex}"
    body = bytearray()
    for field, value in data.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"\r\n\r\n{value}\r\n".encode())
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{audio.name}\"\r\nContent-Type: audio/wav\r\n\r\n".encode())
    body.extend(audio.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    request = Request(
        f"{base_url}/audio/transcriptions", data=bytes(body), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
            request_id = response.headers.get("x-request-id")
    except HTTPError as exc:
        raise RuntimeError(f"OpenAI benchmark request failed ({exc.code})") from exc
    emit(
        "model_loaded", latency_ms=0, checkpoint="api-only",
        checkpoint_sha256="not-downloadable", device="openai-managed",
        compute_type="provider-managed", beam_size=None,
    )
    usage = payload.get("usage")
    emit(
        "provider_metadata", request_id=request_id,
        usage=usage, estimated_cost=estimate_cost(model, usage), actual_cost=None,
        latency_ms=(perf_counter() - started) * 1000,
    )
    emit("final", text=str(payload.get("text", "")).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
