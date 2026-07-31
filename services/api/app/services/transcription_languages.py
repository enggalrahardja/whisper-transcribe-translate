"""Canonical language identity shared by transcription backends and legacy jobs."""

from __future__ import annotations


LANGUAGE_NAME_TO_CODE = {
    "arabic": "ar",
    "chinese": "zh",
    "dutch": "nl",
    "english": "en",
    "french": "fr",
    "german": "de",
    "hindi": "hi",
    "indonesian": "id",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "malay": "ms",
    "portuguese": "pt",
    "russian": "ru",
    "spanish": "es",
    "thai": "th",
    "turkish": "tr",
    "ukrainian": "uk",
    "vietnamese": "vi",
}
LANGUAGE_CODE_TO_LABEL = {
    "ar": "Arabic",
    "zh": "Chinese",
    "nl": "Dutch",
    "en": "English",
    "fr": "French",
    "de": "German",
    "hi": "Hindi",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "ms": "Malay",
    "pt": "Portuguese",
    "ru": "Russian",
    "es": "Spanish",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
}
AUTO_LANGUAGE_VALUES = {"", "auto", "auto-detect", "auto detect"}


class InvalidTranscriptionLanguage(ValueError):
    code = "language_unsupported"
    stage = "validation"

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            f"Unsupported transcription language: {value!r}. Use an ISO language code or a supported language name."
        )

    def structured_details(self) -> dict[str, object]:
        return {"code": self.code, "stage": self.stage, "value": self.value}


def normalize_transcription_language(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace("_", "-")
    if normalized in AUTO_LANGUAGE_VALUES:
        return None
    if normalized in LANGUAGE_CODE_TO_LABEL:
        return normalized
    if normalized in LANGUAGE_NAME_TO_CODE:
        return LANGUAGE_NAME_TO_CODE[normalized]
    raise InvalidTranscriptionLanguage(value)


def transcription_language_label(value: object | None) -> str:
    code = normalize_transcription_language(value)
    return "Auto Detect" if code is None else LANGUAGE_CODE_TO_LABEL[code]


def job_language_code(job: dict) -> str | None:
    raw = job.get("language_code") if "language_code" in job else job.get("language", "auto")
    return normalize_transcription_language(raw)
