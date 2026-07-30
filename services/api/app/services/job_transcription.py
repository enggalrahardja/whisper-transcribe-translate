"""Resolve and apply transcription behavior from one job's stored configuration."""

from __future__ import annotations

import re
from math import exp
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

    raw_segment_count = len(result.get("segments", []))
    segments = [dict(item) for item in result.get("segments", [])]
    if config.processing_mode == "lecture":
        segments = _merge_lecture_segments(segments, config.vad.maximum_segment_duration_seconds)
    glossary = load_job_glossary(config.glossary_id) if config.apply_glossary and config.glossary_id else None
    clean = config.processing_mode == "clean" or config.transcript_style == "clean"

    glossary_corrections_count = 0
    for segment in segments:
        text = str(segment.get("text", ""))
        if glossary is not None:
            correction = glossary.correct(text, language=str(result.get("language") or "auto"))
            text = correction.corrected_text
            glossary_corrections_count += len(correction.corrections)
        if float(segment.get("avg_logprob", 0)) < -1:
            if config.low_confidence_handling == "mark":
                text = f"[low confidence] {text.strip()}"
            elif config.low_confidence_handling == "replace":
                text = "[tidak jelas]"
        segment["text"] = _reduce_fillers(text) if clean else _normalize_whitespace(text)

    segments, paragraphs = group_transcript_segments(segments, config)
    text = "\n\n".join(str(paragraph["text"]) for paragraph in paragraphs)
    if not segments:
        text = str(result.get("text", ""))
        if glossary is not None:
            text = glossary.correct(text, language=str(result.get("language") or "auto")).corrected_text
        if clean:
            text = _format_normalized(_reduce_fillers(text))
        elif config.transcript_style == "verbatim_normalized":
            text = _format_normalized(text)

    diarization_status = _diarization_status(result, config, segments)
    return {
        **result,
        "text": text.strip(),
        "segments": segments,
        "paragraphs": paragraphs,
        "_processing_stats": {
            "raw_segment_count": raw_segment_count,
            "final_segment_count": len(segments),
            "paragraph_count": len(paragraphs),
            "diarization_status": diarization_status,
            "glossary_corrections_count": glossary_corrections_count,
        },
    }


def group_transcript_segments(
    segments: list[dict],
    config: AdvancedTranscriptionSettings,
    *,
    maximum_paragraph_characters: int = 600,
    maximum_paragraph_segments: int = 24,
) -> tuple[list[dict], list[dict]]:
    """Order final segments and create bounded paragraphs without rewriting meaning."""
    ordered = sorted(
        (dict(segment) for segment in segments),
        key=lambda segment: (float(segment.get("start", 0)), float(segment.get("end", 0))),
    )
    pause_threshold = min(1.2, max(0.8, config.vad.minimum_silence_ms / 1000))
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_characters = 0

    for index, segment in enumerate(ordered):
        segment["id"] = segment.get("id", index)
        segment["start"] = float(segment.get("start", 0))
        segment["end"] = max(segment["start"], float(segment.get("end", segment["start"])))
        segment["text"] = _normalize_whitespace(str(segment.get("text", "")))
        segment["confidence"] = _segment_confidence(segment)
        speaker_id = segment.get("speaker_id") or segment.get("speakerId")
        segment["speaker_id"] = str(speaker_id) if speaker_id else None

        previous = current[-1] if current else None
        pause = segment["start"] - float(previous.get("end", segment["start"])) if previous else 0
        speaker_changed = bool(
            previous
            and previous.get("speaker_id")
            and segment.get("speaker_id")
            and previous["speaker_id"] != segment["speaker_id"]
        )
        too_long = bool(
            current
            and (
                current_characters + len(segment["text"]) + 1 > maximum_paragraph_characters
                or len(current) >= maximum_paragraph_segments
            )
        )
        pause_break = bool(current and config.processing_mode in {"interview", "clean"} and pause >= pause_threshold)
        if current and (pause_break or speaker_changed or too_long):
            groups.append(current)
            current = []
            current_characters = 0
        current.append(segment)
        current_characters += len(segment["text"]) + 1
    if current:
        groups.append(current)

    paragraphs: list[dict] = []
    for index, group in enumerate(groups, start=1):
        paragraph_id = f"p-{index:04d}"
        for segment in group:
            segment["paragraph_id"] = paragraph_id
        speaker_ids = {str(segment["speaker_id"]) for segment in group if segment.get("speaker_id")}
        paragraph_text = " ".join(segment["text"] for segment in group if segment["text"])
        if config.transcript_style == "verbatim_normalized" or config.processing_mode == "clean" or config.transcript_style == "clean":
            paragraph_text = _format_normalized(paragraph_text)
        else:
            paragraph_text = _format_verbatim_punctuation(paragraph_text)
        paragraphs.append({
            "id": paragraph_id,
            "start": group[0]["start"],
            "end": group[-1]["end"],
            "text": paragraph_text,
            "speaker_id": next(iter(speaker_ids)) if len(speaker_ids) == 1 else None,
            "segment_ids": [segment["id"] for segment in group],
        })
    return ordered, paragraphs


def _segment_confidence(segment: dict) -> float | None:
    confidence = segment.get("confidence")
    if isinstance(confidence, (int, float)):
        return round(max(0.0, min(1.0, float(confidence))), 6)
    average_log_probability = segment.get("avg_logprob")
    if not isinstance(average_log_probability, (int, float)):
        return None
    return round(max(0.0, min(1.0, exp(float(average_log_probability)))), 6)


def _diarization_status(result: dict, config: AdvancedTranscriptionSettings, segments: list[dict]) -> str:
    if not config.speaker_diarization:
        return "disabled"
    reported = result.get("diarization_status")
    if reported in {"completed", "failed", "unavailable"}:
        return str(reported)
    return "completed" if any(segment.get("speaker_id") for segment in segments) else "unavailable"


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


def _reduce_fillers(text: str) -> str:
    # This is deliberately deterministic: no summarization, paraphrasing, or semantic rewrite.
    value = re.sub(
        r"(?<![\w])(?:uh|um|erm|hmm|eh|anu|eee|mmm)(?![\w])(?:\s*[,;]\s*)?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return _normalize_whitespace(value)


def _format_normalized(text: str) -> str:
    value = _format_verbatim_punctuation(text)
    value = re.sub(
        r"(^|[.!?]\s+)([a-zà-öø-ÿ])",
        lambda match: match.group(1) + match.group(2).upper(),
        value,
    )
    if value and value[-1] not in ".!?)]}\"'":
        value += "."
    return value


def _format_verbatim_punctuation(text: str) -> str:
    value = _normalize_whitespace(text)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([,;:!?])(?=[^\s,.;:!?])", r"\1 ", value)
    return value
