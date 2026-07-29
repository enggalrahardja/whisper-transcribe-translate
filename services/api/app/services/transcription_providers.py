"""Local-first transcription provider contract and optional OpenAI adapters."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import struct
import threading
import wave
from collections import defaultdict, deque
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from time import monotonic, perf_counter
from typing import Awaitable, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from .final_transcription import (
    FinalModelMetadata,
    FinalTranscriber,
    FinalTranscriptionPermanentError,
    FinalTranscriptionRequest,
    FinalTranscriptionResult,
    FinalTranscriptionTimeout,
)


SUPPORTED_FINAL_MODELS = {
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe-diarize",
}


class ProviderConfigurationError(ValueError):
    pass


class ProviderRetryableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelDescription:
    provider_id: str
    model: str
    mode: str
    supports_live: bool
    supports_final: bool
    supports_streaming: bool
    supports_diarization: bool
    downloadable: bool
    privacy_implication: str


@dataclass(frozen=True)
class ProviderLiveEvent:
    item_id: str
    revision: int
    state: str
    text: str
    language: str
    model: str
    request_id: str | None = None


class LiveProviderSession(Protocol):
    async def append_pcm16(self, pcm16: bytes, *, sample_rate: int = 16_000) -> None: ...
    async def commit(self) -> None: ...
    async def close(self) -> None: ...


class TranscriptionProvider(Protocol):
    provider_id: str
    mode: str
    supports_live: bool
    supports_final: bool
    supports_streaming: bool
    supports_diarization: bool

    def transcribe_segment(self, request: FinalTranscriptionRequest, timeout_seconds: float) -> FinalTranscriptionResult: ...
    async def open_live_session(self, callback: Callable[[ProviderLiveEvent], Awaitable[None]]) -> LiveProviderSession: ...
    async def close_live_session(self, session: LiveProviderSession) -> None: ...
    async def health_check(self) -> dict[str, object]: ...
    def estimate_cost(self, model: str, *, duration_seconds: float, usage: dict[str, object] | None = None) -> dict[str, object]: ...
    def describe_model(self, model: str) -> ModelDescription: ...


class LocalTranscriptionProvider:
    """Adapter that preserves the existing local engines behind the contract."""

    provider_id = "local"
    mode = "local"
    supports_live = True
    supports_final = True
    supports_streaming = False
    supports_diarization = False

    def __init__(self, final_transcriber: FinalTranscriber, *, live_session_factory=None) -> None:
        self.final_transcriber = final_transcriber
        self.live_session_factory = live_session_factory

    @property
    def model_load_time_ms(self) -> float:
        return self.final_transcriber.model_load_time_ms

    def transcribe(self, request: FinalTranscriptionRequest, timeout_seconds: float) -> FinalTranscriptionResult:
        return self.transcribe_segment(request, timeout_seconds)

    def transcribe_segment(self, request: FinalTranscriptionRequest, timeout_seconds: float) -> FinalTranscriptionResult:
        return self.final_transcriber.transcribe(request, timeout_seconds)

    async def open_live_session(self, callback: Callable[[ProviderLiveEvent], Awaitable[None]]) -> LiveProviderSession:
        if self.live_session_factory is None:
            raise ProviderConfigurationError("Local live transcription uses the existing PCM/VAD worker")
        return await self.live_session_factory(callback)

    async def close_live_session(self, session: LiveProviderSession) -> None:
        await session.close()

    async def health_check(self) -> dict[str, object]:
        return {"provider": "local", "configured": True, "mode": "local"}

    def estimate_cost(self, model: str, *, duration_seconds: float, usage: dict[str, object] | None = None) -> dict[str, object]:
        return {
            "provider": "local", "model": model, "billingUnit": "request",
            "currency": None, "estimatedAmount": 0.0,
            "limitation": "No per-request model fee; compute and infrastructure still have cost.",
        }

    def describe_model(self, model: str) -> ModelDescription:
        return ModelDescription(
            provider_id="local", model=model, mode="local", supports_live=True,
            supports_final=True, supports_streaming=False, supports_diarization=False,
            downloadable=True, privacy_implication="Audio remains inside the deployment.",
        )


class PricingCatalogue:
    def __init__(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.checked_date = str(payload["checkedDate"])
        self.currency = str(payload.get("currency", "USD"))
        self._entries = {str(item["model"]): dict(item) for item in payload["entries"]}

    def entry(self, model: str) -> dict[str, object]:
        try:
            return dict(self._entries[model])
        except KeyError as exc:
            raise ProviderConfigurationError(f"Pricing is unavailable for model {model}") from exc

    def estimate(self, model: str, *, duration_seconds: float, usage: dict[str, object] | None = None) -> dict[str, object]:
        entry = self.entry(model)
        amount: float | None = None
        if entry["billingUnit"] == "audio_minute":
            amount = max(0.0, duration_seconds) / 60 * float(entry["inputPrice"])
        elif usage:
            input_tokens = float(usage.get("input_tokens", usage.get("inputTokens", 0)) or 0)
            output_tokens = float(usage.get("output_tokens", usage.get("outputTokens", 0)) or 0)
            amount = input_tokens / 1_000_000 * float(entry["inputPrice"])
            amount += output_tokens / 1_000_000 * float(entry.get("outputPrice") or 0)
        return {
            "provider": entry["provider"], "model": model,
            "billingUnit": entry["billingUnit"], "currency": self.currency,
            "estimatedAmount": round(amount, 8) if amount is not None else None,
            "pricingSource": entry["source"], "pricingCheckedDate": self.checked_date,
            "limitation": None if amount is not None else "API usage tokens are required for a token-priced estimate",
        }


@dataclass(frozen=True)
class OpenAIProviderConfig:
    api_key: str = field(repr=False)
    live_model: str = "gpt-realtime-whisper"
    final_model: str = "gpt-4o-transcribe"
    base_url: str = "https://api.openai.com/v1"
    realtime_url: str = "wss://api.openai.com/v1/realtime"
    timeout_seconds: float = 30.0
    max_retries: int = 2
    rate_limit_per_minute: int = 30
    external_audio_consent: bool = False

    def validate(self) -> None:
        if not self.api_key.strip():
            raise ProviderConfigurationError("OPENAI_API_KEY is required")
        if not self.external_audio_consent:
            raise ProviderConfigurationError("Explicit external-audio consent is required")
        if self.final_model not in SUPPORTED_FINAL_MODELS:
            raise ProviderConfigurationError(f"Unsupported OpenAI final model: {self.final_model}")
        if self.live_model != "gpt-realtime-whisper":
            raise ProviderConfigurationError(f"Unsupported OpenAI live model: {self.live_model}")
        if self.timeout_seconds <= 0 or self.max_retries not in range(0, 11):
            raise ProviderConfigurationError("OpenAI timeout/retry configuration is invalid")
        if self.rate_limit_per_minute <= 0:
            raise ProviderConfigurationError("OpenAI rate limit must be positive")


@dataclass(frozen=True)
class OpenAIResponse:
    payload: dict[str, object]
    request_id: str | None


class OpenAIHttpTransport:
    """Server-side HTTP transport. The API key is only attached here."""

    def __init__(self, config: OpenAIProviderConfig) -> None:
        self.config = config

    def transcribe(self, audio_wav: bytes, *, model: str, language: str, prompt: str | None, timeout_seconds: float) -> OpenAIResponse:
        data = {"model": model, "response_format": "json"}
        if language and language != "auto":
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        boundary = f"----transcription-{uuid4().hex}"
        body = bytearray()
        for key, value in data.items():
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
        body.extend(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"segment.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
        )
        body.extend(audio_wav)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        request = Request(
            f"{self.config.base_url.rstrip('/')}/audio/transcriptions",
            data=bytes(body), method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read())
                request_id = response.headers.get("x-request-id")
        except (TimeoutError, socket.timeout) as exc:
            raise FinalTranscriptionTimeout("OpenAI transcription timed out") from exc
        except HTTPError as exc:
            if exc.code in {401, 403, 404, 422}:
                raise FinalTranscriptionPermanentError(f"OpenAI rejected the request ({exc.code})") from exc
            if exc.code in {408, 409, 429} or exc.code >= 500:
                raise ProviderRetryableError(f"OpenAI temporary failure ({exc.code})") from exc
            raise FinalTranscriptionPermanentError(f"OpenAI request failed ({exc.code})") from exc
        except URLError as exc:
            raise ProviderRetryableError("OpenAI transport failed") from exc
        if not isinstance(payload, dict):
            raise FinalTranscriptionPermanentError("OpenAI returned an invalid response")
        return OpenAIResponse(payload, request_id)


class _MinuteRateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        now = monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] >= 60:
                self._calls.popleft()
            if len(self._calls) >= self.limit:
                raise ProviderRetryableError("OpenAI provider rate limit reached")
            self._calls.append(now)


def wav_duration_seconds(audio_wav: bytes) -> float:
    with wave.open(BytesIO(audio_wav), "rb") as source:
        return source.getnframes() / source.getframerate()


class OpenAIFinalTranscriber(FinalTranscriber):
    model_load_time_ms = 0.0

    def __init__(self, config: OpenAIProviderConfig, catalogue: PricingCatalogue, *, transport: OpenAIHttpTransport | None = None, local_fallback: FinalTranscriber | None = None) -> None:
        config.validate()
        self.config = config
        self.catalogue = catalogue
        self.transport = transport or OpenAIHttpTransport(config)
        self.local_fallback = local_fallback
        self._rate = _MinuteRateLimiter(config.rate_limit_per_minute)

    def transcribe(self, request: FinalTranscriptionRequest, timeout_seconds: float) -> FinalTranscriptionResult:
        started = perf_counter()
        duration = wav_duration_seconds(request.audio_wav)
        retry = 0
        while True:
            try:
                self._rate.acquire()
                response = self.transport.transcribe(
                    request.audio_wav, model=self.config.final_model,
                    language=request.language,
                    prompt=getattr(request.glossary, "prompt_context", None),
                    timeout_seconds=min(timeout_seconds, self.config.timeout_seconds),
                )
                break
            except (ProviderRetryableError, FinalTranscriptionTimeout):
                if retry >= self.config.max_retries:
                    if self.local_fallback is not None:
                        return self.local_fallback.transcribe(request, timeout_seconds)
                    raise
                retry += 1
        raw_text = str(response.payload.get("text", "")).strip()
        if not raw_text:
            raise FinalTranscriptionPermanentError("OpenAI accurate-final transcription was empty")
        correction = request.glossary.correct(raw_text, language=request.language) if request.glossary is not None else None
        usage = response.payload.get("usage")
        usage_dict = dict(usage) if isinstance(usage, dict) else None
        language = str(response.payload.get("language") or request.language)
        latency_ms = (perf_counter() - started) * 1000
        return FinalTranscriptionResult(
            text=correction.text if correction else raw_text,
            raw_text=raw_text,
            glossary_corrections=correction.corrections if correction else (),
            glossary_version=correction.glossary_version if correction else None,
            metadata=FinalModelMetadata(
                model=self.config.final_model, checkpoint_path="api-only", checkpoint_sha256="not-downloadable",
                device="openai-managed", compute_type="provider-managed", language=language,
                beam_size=0, timestamps=(), latency_ms=latency_ms, provider="openai", local_cloud="cloud",
                api_request_id=response.request_id, duration_seconds=duration, usage=usage_dict,
                estimated_cost=self.catalogue.estimate(self.config.final_model, duration_seconds=duration, usage=usage_dict),
                retry_count=retry,
            ),
        )


class OpenAILiveEventMapper:
    def __init__(self, model: str) -> None:
        self.model = model
        self._revisions: defaultdict[str, int] = defaultdict(int)
        self._text: defaultdict[str, str] = defaultdict(str)
        self._final: set[str] = set()

    def map(self, event: dict[str, object]) -> ProviderLiveEvent | None:
        event_type = str(event.get("type", ""))
        if event_type not in {
            "conversation.item.input_audio_transcription.delta",
            "conversation.item.input_audio_transcription.stable",
            "conversation.item.input_audio_transcription.completed",
        }:
            return None
        item_id = str(event.get("item_id") or event.get("itemId") or "current")
        if item_id in self._final:
            return None
        if event_type.endswith(".delta"):
            self._text[item_id] += str(event.get("delta", ""))
            state = "stable" if bool(event.get("stable")) else "partial"
            text = self._text[item_id]
        elif event_type.endswith(".stable"):
            state, text = "stable", str(event.get("transcript") or self._text[item_id])
            self._text[item_id] = text
        else:
            state, text = "final", str(event.get("transcript") or self._text[item_id])
            self._final.add(item_id)
        self._revisions[item_id] += 1
        return ProviderLiveEvent(
            item_id=item_id, revision=self._revisions[item_id], state=state, text=text,
            language=str(event.get("language") or "auto"), model=self.model,
            request_id=str(event.get("request_id")) if event.get("request_id") else None,
        )


def resample_pcm16_mono(pcm16: bytes, source_rate: int, target_rate: int = 24_000) -> bytes:
    if source_rate <= 0 or len(pcm16) % 2:
        raise ValueError("PCM16 mono input is malformed")
    if source_rate == target_rate or not pcm16:
        return pcm16
    samples = struct.unpack(f"<{len(pcm16) // 2}h", pcm16)
    target_count = max(1, round(len(samples) * target_rate / source_rate))
    output: list[int] = []
    for index in range(target_count):
        position = index * source_rate / target_rate
        left = min(int(position), len(samples) - 1)
        right = min(left + 1, len(samples) - 1)
        fraction = position - left
        output.append(round(samples[left] + (samples[right] - samples[left]) * fraction))
    return struct.pack(f"<{len(output)}h", *output)


class OpenAIRealtimeSession:
    def __init__(self, websocket: object, config: OpenAIProviderConfig, callback: Callable[[ProviderLiveEvent], Awaitable[None]]) -> None:
        self.websocket = websocket
        self.config = config
        self.callback = callback
        self.mapper = OpenAILiveEventMapper(config.live_model)
        self._reader = asyncio.create_task(self._read(), name="openai-realtime-reader")

    async def initialize(self) -> None:
        await self.websocket.send(json.dumps({
            "type": "session.update", "session": {"type": "transcription", "audio": {"input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "transcription": {"model": self.config.live_model}, "turn_detection": None,
            }}}
        }))

    async def append_pcm16(self, pcm16: bytes, *, sample_rate: int = 16_000) -> None:
        converted = resample_pcm16_mono(pcm16, sample_rate, 24_000)
        await self.websocket.send(json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(converted).decode("ascii")}))

    async def commit(self) -> None:
        await self.websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))

    async def close(self) -> None:
        self._reader.cancel()
        await asyncio.gather(self._reader, return_exceptions=True)
        await self.websocket.close()

    async def _read(self) -> None:
        async for raw in self.websocket:
            event = json.loads(raw)
            mapped = self.mapper.map(event) if isinstance(event, dict) else None
            if mapped is not None:
                await self.callback(mapped)


class OpenAITranscriptionProvider:
    provider_id = "openai"
    mode = "cloud"
    supports_live = True
    supports_final = True
    supports_streaming = True
    supports_diarization = True

    def __init__(self, config: OpenAIProviderConfig, catalogue: PricingCatalogue, *, final_transport: OpenAIHttpTransport | None = None, local_fallback: FinalTranscriber | None = None, websocket_connect: Callable[..., Awaitable[object]] | None = None) -> None:
        config.validate()
        self.config = config
        self.catalogue = catalogue
        self.final_transcriber = OpenAIFinalTranscriber(config, catalogue, transport=final_transport, local_fallback=local_fallback)
        self.websocket_connect = websocket_connect

    def transcribe_segment(self, request: FinalTranscriptionRequest, timeout_seconds: float) -> FinalTranscriptionResult:
        return self.final_transcriber.transcribe(request, timeout_seconds)

    async def open_live_session(self, callback: Callable[[ProviderLiveEvent], Awaitable[None]]) -> OpenAIRealtimeSession:
        if self.websocket_connect is None:
            from websockets.asyncio.client import connect
            connector = connect
        else:
            connector = self.websocket_connect
        websocket = await connector(
            f"{self.config.realtime_url}?model={self.config.live_model}",
            additional_headers={"Authorization": f"Bearer {self.config.api_key}"},
            open_timeout=self.config.timeout_seconds,
        )
        session = OpenAIRealtimeSession(websocket, self.config, callback)
        await session.initialize()
        return session

    async def close_live_session(self, session: LiveProviderSession) -> None:
        await session.close()

    async def health_check(self) -> dict[str, object]:
        return {"provider": self.provider_id, "configured": bool(self.config.api_key), "mode": self.mode}

    def estimate_cost(self, model: str, *, duration_seconds: float, usage: dict[str, object] | None = None) -> dict[str, object]:
        return self.catalogue.estimate(model, duration_seconds=duration_seconds, usage=usage)

    def describe_model(self, model: str) -> ModelDescription:
        if model != self.config.live_model and model not in SUPPORTED_FINAL_MODELS:
            raise ProviderConfigurationError(f"Unsupported OpenAI transcription model: {model}")
        return ModelDescription(
            provider_id="openai", model=model, mode="cloud",
            supports_live=model == self.config.live_model,
            supports_final=model in SUPPORTED_FINAL_MODELS,
            supports_streaming=model == self.config.live_model,
            supports_diarization=model == "gpt-4o-transcribe-diarize",
            downloadable=False,
            privacy_implication="Audio leaves the deployment and is processed by OpenAI.",
        )


def require_explicit_provider(provider: str, *, api_key: str, consent: bool) -> str:
    normalized = provider.strip().lower()
    if normalized not in {"local", "openai"}:
        raise ProviderConfigurationError("Provider must be local or openai")
    if normalized == "openai" and (not api_key.strip() or not consent):
        raise ProviderConfigurationError("OpenAI requires an API key and explicit external-audio consent")
    return normalized
