"""Resolve and apply transcription behavior from one job's stored configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models.job import AdvancedTranscriptionSettings
from .job_glossaries import load_job_glossary


@dataclass(frozen=True)
class JobInferenceOptions:
    language: str
    beam_size: int
    best_of: int | None
    temperature: float
    word_timestamps: bool
    condition_on_previous_text: bool
    no_speech_threshold: float | None
    initial_prompt: str


def parse_job_config(job: dict) -> AdvancedTranscriptionSettings | None:
    raw = job.get("transcription_config")
    return AdvancedTranscriptionSettings.model_validate(raw) if isinstance(raw, dict) else None


def inference_options(job: dict, application_settings: object) -> JobInferenceOptions:
    """Use application defaults only for legacy/Standard values not overridden per job."""
    config = parse_job_config(job)
    transcription = application_settings.transcription
    if config is None:
        return JobInferenceOptions(
            language=str(job.get("language", "auto")),
            beam_size=transcription.beam_size,
            best_of=None,
            temperature=transcription.temperature,
            word_timestamps=transcription.word_timestamps,
            condition_on_previous_text=True,
            no_speech_threshold=0.6,
            initial_prompt=transcription.initial_prompt,
        )

    accurate = config.accurate
    prompt = transcription.initial_prompt
    if config.apply_glossary and config.glossary_id:
        glossary_prompt = load_job_glossary(config.glossary_id).prompt_context
        prompt = "\n".join(value for value in (prompt.strip(), glossary_prompt.strip()) if value)
    return JobInferenceOptions(
        language=str(job.get("language", "auto")) if config.force_language else "auto",
        beam_size=accurate.beam_size if config.accurate_final else transcription.beam_size,
        best_of=accurate.best_of if config.accurate_final else None,
        temperature=accurate.temperature if config.accurate_final else transcription.temperature,
        word_timestamps=accurate.word_timestamps if config.accurate_final else transcription.word_timestamps,
        condition_on_previous_text=config.use_previous_segment_context,
        no_speech_threshold=0.6 if config.use_vad else None,
        initial_prompt=prompt,
    )


def apply_job_output_config(result: dict, job: dict) -> dict:
    config = parse_job_config(job)
    if config is None:
        return result

    if (
        config.processing_mode == "standard"
        and not config.apply_glossary
        and config.transcript_style == "verbatim"
        and config.low_confidence_handling == "keep"
    ):
        return result

    segments = [dict(item) for item in result.get("segments", [])]
    if config.processing_mode == "lecture":
        segments = _merge_lecture_segments(segments, config.vad.maximum_segment_duration_seconds)
    glossary = load_job_glossary(config.glossary_id) if config.apply_glossary and config.glossary_id else None
    clean = config.processing_mode == "clean" or config.transcript_style == "clean"

    for segment in segments:
        text = str(segment.get("text", ""))
        if glossary is not None:
            text = glossary.correct(text, language=str(result.get("language") or "auto")).corrected_text
        if float(segment.get("avg_logprob", 0)) < -1:
            if config.low_confidence_handling == "mark":
                text = f"[low confidence] {text.strip()}"
            elif config.low_confidence_handling == "replace":
                text = "[tidak jelas]"
        if clean:
            text = _clean_transcript(text)
        elif config.transcript_style == "verbatim_normalized":
            text = _normalize_whitespace(text)
        segment["text"] = text

    text = _join_segments(segments, config)
    if not segments:
        text = str(result.get("text", ""))
        if glossary is not None:
            text = glossary.correct(text, language=str(result.get("language") or "auto")).corrected_text
        if clean:
            text = _clean_transcript(text)
        elif config.transcript_style == "verbatim_normalized":
            text = _normalize_whitespace(text)

    return {**result, "text": text.strip(), "segments": segments}


def _join_segments(segments: list[dict], config: AdvancedTranscriptionSettings) -> str:
    chunks: list[str] = []
    previous_end: float | None = None
    pause_threshold = config.vad.minimum_silence_ms / 1000
    paragraph_by_pause = config.processing_mode in {"interview", "clean"}
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        start = float(segment.get("start", previous_end or 0))
        separator = "\n\n" if paragraph_by_pause and previous_end is not None and start - previous_end >= pause_threshold else " "
        if chunks:
            chunks.append(separator)
        chunks.append(text)
        previous_end = float(segment.get("end", start))
    return "".join(chunks)


def _merge_lecture_segments(segments: list[dict], maximum_duration: float) -> list[dict]:
    """Allow longer presentation segments without changing their spoken content."""
    merged: list[dict] = []
    for segment in segments:
        if not merged:
            merged.append(segment)
            continue
        previous = merged[-1]
        combined_duration = float(segment.get("end", 0)) - float(previous.get("start", 0))
        pause = float(segment.get("start", 0)) - float(previous.get("end", 0))
        if combined_duration <= maximum_duration and pause < 1.5:
            previous["end"] = segment.get("end", previous.get("end"))
            previous["text"] = f"{str(previous.get('text', '')).rstrip()} {str(segment.get('text', '')).lstrip()}"
            previous["avg_logprob"] = min(float(previous.get("avg_logprob", 0)), float(segment.get("avg_logprob", 0)))
            if isinstance(previous.get("words"), list) and isinstance(segment.get("words"), list):
                previous["words"] = [*previous["words"], *segment["words"]]
            continue
        merged.append(segment)
    return merged


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text, flags=re.UNICODE).strip()


def _clean_transcript(text: str) -> str:
    # This is deliberately deterministic: no summarization, paraphrasing, or semantic rewrite.
    value = re.sub(
        r"(?<![\w])(?:uh|um|erm|hmm|eh|anu|eee|mmm)(?![\w])(?:\s*[,;]\s*)?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    value = _normalize_whitespace(value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    if value and value[-1] not in ".!?)]}\"'":
        value += "."
    return value
