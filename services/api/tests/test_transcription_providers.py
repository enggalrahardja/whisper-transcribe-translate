import asyncio
import json
import struct
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import wave
from io import BytesIO
from pathlib import Path

from app.config import Settings, validate_startup_configuration
from app.services.final_transcription import (
    FinalModelMetadata,
    FinalTranscriptionPermanentError,
    FinalTranscriptionRequest,
    FinalTranscriptionResult,
    FinalTranscriptionTimeout,
)
from app.services.transcription_providers import (
    OpenAIFinalTranscriber,
    OpenAIHttpTransport,
    OpenAIProviderConfig,
    OpenAIRealtimeSession,
    OpenAIResponse,
    OpenAITranscriptionProvider,
    OpenAILiveEventMapper,
    LocalTranscriptionProvider,
    PricingCatalogue,
    ProviderConfigurationError,
    ProviderRetryableError,
    require_explicit_provider,
    resample_pcm16_mono,
)


ROOT = Path(__file__).resolve().parents[3]
CATALOGUE = PricingCatalogue(ROOT / "config/openai-pricing.json")


def wav(seconds: float = 0.1) -> bytes:
    stream = BytesIO()
    with wave.open(stream, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(struct.pack("<h", 100) * int(16_000 * seconds))
    return stream.getvalue()


def request() -> FinalTranscriptionRequest:
    return FinalTranscriptionRequest(
        session_id="session-a", segment_id="segment-a", sequence_start=0,
        sequence_end=9, start_ms=0, end_ms=100, language="id", audio_wav=wav(),
    )


def config(**changes) -> OpenAIProviderConfig:
    values = {
        "api_key": "server-secret", "external_audio_consent": True,
        "max_retries": 1, "rate_limit_per_minute": 10,
    }
    values.update(changes)
    return OpenAIProviderConfig(**values)


class Transport:
    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def transcribe(self, *args, **kwargs):
        self.calls += 1
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class LocalFallback:
    model_load_time_ms = 0.0

    def transcribe(self, request, timeout_seconds):
        return FinalTranscriptionResult(
            text="local preserved", raw_text="local preserved",
            metadata=FinalModelMetadata(
                model="base", checkpoint_path="base.pt", checkpoint_sha256="sha",
                device="cpu", compute_type="float32", language="id", beam_size=5,
                timestamps=(), latency_ms=1,
            ),
        )


def test_local_is_default_and_cloud_requires_explicit_key_and_consent():
    settings = Settings(_env_file=None)
    assert settings.live_transcription_provider == "local"
    assert settings.live_final_provider == "local"
    assert require_explicit_provider("local", api_key="", consent=False) == "local"
    with unittest.TestCase().assertRaises(ProviderConfigurationError):
        require_explicit_provider("openai", api_key="", consent=True)
    with unittest.TestCase().assertRaises(ProviderConfigurationError):
        require_explicit_provider("openai", api_key="key", consent=False)
    local = LocalTranscriptionProvider(LocalFallback())
    assert local.transcribe_segment(request(), 1).text == "local preserved"
    assert local.estimate_cost("base", duration_seconds=1)["estimatedAmount"] == 0


def test_production_validation_rejects_implicit_or_private_cloud():
    with unittest.TestCase().assertRaisesRegex(ValueError, "API_KEY"):
        validate_startup_configuration(Settings(_env_file=None, live_final_provider="openai"))
    with unittest.TestCase().assertRaisesRegex(ValueError, "Private"):
        validate_startup_configuration(Settings(
            _env_file=None, live_final_provider="openai", openai_api_key="secret",
            openai_external_audio_consent=True, security_profile="Private",
        ))


def test_live_event_mapping_is_monotonic_and_final_is_immutable():
    mapper = OpenAILiveEventMapper("gpt-realtime-whisper")
    partial = mapper.map({"type": "conversation.item.input_audio_transcription.delta", "item_id": "a", "delta": "Halo "})
    stable = mapper.map({"type": "conversation.item.input_audio_transcription.stable", "item_id": "a", "transcript": "Halo dunia"})
    final = mapper.map({"type": "conversation.item.input_audio_transcription.completed", "item_id": "a", "transcript": "Halo dunia."})
    duplicate = mapper.map({"type": "conversation.item.input_audio_transcription.completed", "item_id": "a", "transcript": "changed"})
    assert [partial.state, stable.state, final.state] == ["partial", "stable", "final"]
    assert [partial.revision, stable.revision, final.revision] == [1, 2, 3]
    assert duplicate is None


def test_provider_boundary_resamples_without_changing_internal_contract():
    source = struct.pack("<4h", -100, 0, 100, 200)
    converted = resample_pcm16_mono(source, 16_000, 24_000)
    assert len(converted) == 12
    assert source == struct.pack("<4h", -100, 0, 100, 200)


def test_accurate_final_metadata_pricing_and_request_id():
    transport = Transport([OpenAIResponse({"text": "hasil", "language": "id", "usage": {"input_tokens": 1000, "output_tokens": 100}}, "req_123")])
    result = OpenAIFinalTranscriber(config(), CATALOGUE, transport=transport).transcribe(request(), 5)
    metadata = result.metadata.as_dict()
    assert result.text == "hasil"
    assert metadata["provider"] == "openai"
    assert metadata["localCloud"] == "cloud"
    assert metadata["apiRequestId"] == "req_123"
    assert metadata["retryCount"] == 0
    assert metadata["estimatedCost"]["estimatedAmount"] is not None
    assert "server-secret" not in json.dumps(metadata)


def test_retryable_error_retries_but_invalid_key_does_not():
    transport = Transport([ProviderRetryableError("rate"), OpenAIResponse({"text": "ok"}, None)])
    result = OpenAIFinalTranscriber(config(), CATALOGUE, transport=transport).transcribe(request(), 5)
    assert result.metadata.retry_count == 1
    assert transport.calls == 2
    invalid = Transport([FinalTranscriptionPermanentError("invalid key")])
    with unittest.TestCase().assertRaises(FinalTranscriptionPermanentError):
        OpenAIFinalTranscriber(config(), CATALOGUE, transport=invalid).transcribe(request(), 5)
    assert invalid.calls == 1


def test_http_invalid_api_key_is_permanent_and_timeout_is_bounded():
    error = HTTPError("https://api.openai.com", 401, "unauthorized", {}, None)
    with patch("app.services.transcription_providers.urlopen", side_effect=error):
        with unittest.TestCase().assertRaises(FinalTranscriptionPermanentError):
            OpenAIHttpTransport(config()).transcribe(
                wav(), model="gpt-4o-transcribe", language="id", prompt=None,
                timeout_seconds=1,
            )
    timed = Transport([FinalTranscriptionTimeout("slow"), FinalTranscriptionTimeout("slow")])
    with unittest.TestCase().assertRaises(FinalTranscriptionTimeout):
        OpenAIFinalTranscriber(config(), CATALOGUE, transport=timed).transcribe(request(), 1)
    assert timed.calls == 2


def test_explicit_local_fallback_preserves_result_and_no_implicit_cloud_fallback():
    failing = Transport([ProviderRetryableError("down"), ProviderRetryableError("down")])
    result = OpenAIFinalTranscriber(config(), CATALOGUE, transport=failing, local_fallback=LocalFallback()).transcribe(request(), 5)
    assert result.text == "local preserved"
    assert result.metadata.local_cloud == "local"
    without_fallback = Transport([ProviderRetryableError("down"), ProviderRetryableError("down")])
    with unittest.TestCase().assertRaises(ProviderRetryableError):
        OpenAIFinalTranscriber(config(), CATALOGUE, transport=without_fallback).transcribe(request(), 5)


def test_rate_limit_is_bounded():
    transport = Transport([OpenAIResponse({"text": "one"}, None), OpenAIResponse({"text": "two"}, None)])
    transcriber = OpenAIFinalTranscriber(config(rate_limit_per_minute=1, max_retries=0), CATALOGUE, transport=transport)
    transcriber.transcribe(request(), 5)
    with unittest.TestCase().assertRaisesRegex(ProviderRetryableError, "rate limit"):
        transcriber.transcribe(request(), 5)


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.events = asyncio.Queue()
        self.closed = False

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.events.get()
        if value is None:
            raise StopAsyncIteration
        return json.dumps(value)


async def test_server_side_realtime_session_contract_and_secret_isolation():
    socket = FakeWebSocket()
    calls = []

    async def connect(url, **kwargs):
        calls.append((url, kwargs))
        return socket

    provider = OpenAITranscriptionProvider(config(), CATALOGUE, websocket_connect=connect)
    events = []
    async def capture(event):
        events.append(event)
    session = await provider.open_live_session(capture)
    await session.append_pcm16(struct.pack("<1600h", *([0] * 1600)))
    await session.commit()
    await socket.events.put({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i", "delta": "hi"})
    await asyncio.sleep(0)
    assert socket.sent[0]["type"] == "session.update"
    assert socket.sent[1]["type"] == "input_audio_buffer.append"
    assert socket.sent[2]["type"] == "input_audio_buffer.commit"
    assert calls[0][1]["additional_headers"]["Authorization"] == "Bearer server-secret"
    assert "server-secret" not in json.dumps(socket.sent)
    assert events[0].state == "partial"
    await session.close()


def test_pricing_duration_estimate_and_privacy_description():
    estimate = CATALOGUE.estimate("gpt-realtime-whisper", duration_seconds=120)
    unittest.TestCase().assertAlmostEqual(estimate["estimatedAmount"], 0.034)
    provider = OpenAITranscriptionProvider(config(), CATALOGUE, websocket_connect=lambda *a, **k: None)
    description = provider.describe_model("gpt-realtime-whisper")
    assert description.downloadable is False
    assert "leaves" in description.privacy_implication


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    synchronous = [
        test_local_is_default_and_cloud_requires_explicit_key_and_consent,
        test_production_validation_rejects_implicit_or_private_cloud,
        test_live_event_mapping_is_monotonic_and_final_is_immutable,
        test_provider_boundary_resamples_without_changing_internal_contract,
        test_accurate_final_metadata_pricing_and_request_id,
        test_retryable_error_retries_but_invalid_key_does_not,
        test_http_invalid_api_key_is_permanent_and_timeout_is_bounded,
        test_explicit_local_fallback_preserves_result_and_no_implicit_cloud_fallback,
        test_rate_limit_is_bounded,
        test_pricing_duration_estimate_and_privacy_description,
    ]
    suite.addTests(unittest.FunctionTestCase(item) for item in synchronous)
    suite.addTest(unittest.FunctionTestCase(
        lambda: asyncio.run(test_server_side_realtime_session_contract_and_secret_isolation())
    ))
    return suite
