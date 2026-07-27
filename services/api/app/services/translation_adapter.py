from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from time import sleep

from deep_translator import GoogleTranslator

from .application_settings import get_application_settings

MAX_TRANSLATION_CHARS = 4500


def _supported_languages() -> dict[str, str]:
    languages = GoogleTranslator().get_supported_languages(as_dict=True)
    return {str(name).lower(): str(code).lower() for name, code in languages.items()}


SUPPORTED_TARGET_LANGUAGES = _supported_languages()
SUPPORTED_TARGET_CODES = set(SUPPORTED_TARGET_LANGUAGES.values())


def normalize_target_language(target_language: str | None) -> str:
    normalized = (target_language or "").strip().lower()
    if not normalized:
        raise ValueError("target_language is required for translate jobs")
    if normalized not in SUPPORTED_TARGET_LANGUAGES and normalized not in SUPPORTED_TARGET_CODES:
        raise ValueError(f"Unsupported target language: {target_language}")
    return normalized


def _split_text(text: str, maximum_length: int = MAX_TRANSLATION_CHARS) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for word in words:
        if len(word) > maximum_length:
            if current:
                chunks.append(" ".join(current))
                current = []
                current_length = 0
            chunks.extend(word[index:index + maximum_length] for index in range(0, len(word), maximum_length))
            continue

        next_length = current_length + len(word) + (1 if current else 0)
        if current and next_length > maximum_length:
            chunks.append(" ".join(current))
            current = [word]
            current_length = len(word)
        else:
            current.append(word)
            current_length = next_length

    if current:
        chunks.append(" ".join(current))
    return chunks


class TranslationAdapter:
    """Headless adapter around the translation provider used by the desktop app."""

    def translate(
        self,
        text: str,
        target_language: str,
        cancel_callback: Callable[[], bool] | None = None,
    ) -> str:
        original_text = text.strip()
        if not original_text:
            raise ValueError("Transcription is empty; there is no text to translate")

        settings = get_application_settings().translation
        if settings.translation_provider != "google":
            raise RuntimeError(f"Unsupported translation provider: {settings.translation_provider}")
        target = normalize_target_language(target_language)
        translated_chunks: list[str] = []

        try:
            for chunk in _split_text(original_text, settings.max_chunk_length):
                if cancel_callback and cancel_callback():
                    raise InterruptedError("Translation was interrupted")
                translated = None
                last_error: Exception | None = None
                for attempt in range(settings.retry_count + 1):
                    if cancel_callback and cancel_callback():
                        raise InterruptedError("Translation was interrupted")
                    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="translation-provider")
                    future = executor.submit(GoogleTranslator(source="auto", target=target).translate, text=chunk)
                    try:
                        translated = future.result(timeout=settings.provider_timeout_seconds)
                        last_error = None
                        executor.shutdown(wait=True)
                        break
                    except FutureTimeoutError as exc:
                        future.cancel()
                        last_error = TimeoutError(
                            f"Translation provider timed out after {settings.provider_timeout_seconds:g} seconds"
                        )
                        executor.shutdown(wait=False, cancel_futures=True)
                    except Exception as exc:
                        last_error = exc
                        executor.shutdown(wait=False, cancel_futures=True)
                    if attempt < settings.retry_count:
                        sleep(min(2.0, 0.5 * (attempt + 1)))
                if last_error is not None:
                    raise last_error
                if not translated or not str(translated).strip():
                    raise RuntimeError("Translation provider returned an empty result")
                translated_chunks.append(str(translated).strip())
        except InterruptedError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Translation provider failed: {exc}") from exc

        if cancel_callback and cancel_callback():
            raise InterruptedError("Translation was interrupted")
        return "\n".join(translated_chunks)
