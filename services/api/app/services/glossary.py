"""Local glossary snapshots, prompt context, and deterministic whole-word correction."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Callable


@dataclass(frozen=True)
class GlossaryTerm:
    preferred_spelling: str
    aliases: tuple[str, ...]
    do_not_change: bool
    category: str
    priority: int
    language: str
    active: bool
    preferred_translations: tuple[tuple[str, str], ...] = ()
    do_not_translate: bool = False

    def preferred_translation(self, language: str) -> str | None:
        requested = language.casefold().split("-", 1)[0]
        for target, value in self.preferred_translations:
            if target.casefold().split("-", 1)[0] == requested:
                return value
        return None


@dataclass(frozen=True)
class GlossaryCorrection:
    source: str
    replacement: str
    start: int
    end: int
    category: str
    priority: int
    language: str

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "replacement": self.replacement,
            "start": self.start,
            "end": self.end,
            "category": self.category,
            "priority": self.priority,
            "language": self.language,
        }


@dataclass(frozen=True)
class GlossaryCorrectionResult:
    raw_text: str
    corrected_text: str
    corrections: tuple[GlossaryCorrection, ...]
    glossary_version: str | None
    latency_ms: float


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    matched: str
    replacement: str
    term: GlossaryTerm
    alias_key: str | None


MetricsRecorder = Callable[[int, bool, int, int, float], None]


@dataclass(frozen=True)
class GlossarySnapshot:
    version: str
    terms: tuple[GlossaryTerm, ...]
    prompt_context: str
    _record_metrics: MetricsRecorder

    def correct(
        self,
        text: str,
        *,
        language: str,
        record_metrics: bool = True,
    ) -> GlossaryCorrectionResult:
        started = perf_counter()
        candidates: list[_Candidate] = []
        matched_aliases: set[tuple[str, str]] = set()
        applicable = tuple(term for term in self.terms if _language_matches(term.language, language))
        alias_count = sum(len(term.aliases) for term in applicable)

        for term in applicable:
            forms = ((term.preferred_spelling, None),) + tuple(
                (alias, alias.casefold()) for alias in term.aliases
            )
            seen_forms: set[str] = set()
            for form, alias_key in forms:
                normalized = form.casefold()
                if not form or normalized in seen_forms:
                    continue
                seen_forms.add(normalized)
                pattern = _whole_term_pattern(form)
                for match in pattern.finditer(text):
                    if alias_key is not None:
                        matched_aliases.add((term.preferred_spelling, alias_key))
                    candidates.append(
                        _Candidate(
                            start=match.start(),
                            end=match.end(),
                            matched=match.group(0),
                            replacement=match.group(0) if term.do_not_change else term.preferred_spelling,
                            term=term,
                            alias_key=alias_key,
                        )
                    )

        unique: dict[tuple[int, int, str], _Candidate] = {}
        for candidate in candidates:
            key = (candidate.start, candidate.end, candidate.term.preferred_spelling)
            existing = unique.get(key)
            if existing is None or candidate.term.priority > existing.term.priority:
                unique[key] = candidate
        ordered = sorted(
            unique.values(),
            key=lambda item: (-item.term.priority, -(item.end - item.start), item.start),
        )
        accepted: list[_Candidate] = []
        conflicts = 0
        for candidate in ordered:
            if any(candidate.start < item.end and item.start < candidate.end for item in accepted):
                conflicts += 1
                continue
            accepted.append(candidate)

        corrections: list[GlossaryCorrection] = []
        corrected = text
        for candidate in sorted(accepted, key=lambda item: item.start, reverse=True):
            if candidate.matched == candidate.replacement:
                continue
            corrected = corrected[:candidate.start] + candidate.replacement + corrected[candidate.end:]
            corrections.append(
                GlossaryCorrection(
                    source=candidate.matched,
                    replacement=candidate.replacement,
                    start=candidate.start,
                    end=candidate.end,
                    category=candidate.term.category,
                    priority=candidate.term.priority,
                    language=candidate.term.language,
                )
            )
        corrections.sort(key=lambda item: item.start)
        latency_ms = (perf_counter() - started) * 1000
        unmatched = max(0, alias_count - len(matched_aliases))
        if record_metrics:
            self._record_metrics(
                len(corrections),
                bool(corrections),
                unmatched,
                conflicts,
                latency_ms,
            )
        return GlossaryCorrectionResult(
            raw_text=text,
            corrected_text=corrected,
            corrections=tuple(corrections),
            glossary_version=self.version,
            latency_ms=latency_ms,
        )


class DisabledGlossarySnapshot:
    version = None
    terms: tuple[GlossaryTerm, ...] = ()
    prompt_context = ""

    def correct(
        self,
        text: str,
        *,
        language: str,
        record_metrics: bool = True,
    ) -> GlossaryCorrectionResult:
        del language
        del record_metrics
        return GlossaryCorrectionResult(text, text, (), None, 0.0)


class GlossaryManager:
    def __init__(
        self,
        path: Path,
        *,
        enabled: bool,
        prompt_max_terms: int = 64,
    ) -> None:
        if prompt_max_terms < 1:
            raise ValueError("Glossary prompt term limit must be positive")
        self.path = path
        self.enabled = enabled
        self.prompt_max_terms = prompt_max_terms
        self._snapshot: GlossarySnapshot | DisabledGlossarySnapshot | None = None
        self._lock = RLock()
        self._metrics: dict[str, int | float] = {
            "glossary_terms_loaded": 0,
            "corrections_applied": 0,
            "segments_corrected": 0,
            "unmatched_aliases": 0,
            "correction_latency_total_ms": 0.0,
            "correction_calls": 0,
            "glossary_reload_count": 0,
            "correction_conflicts": 0,
        }

    def snapshot(self) -> GlossarySnapshot | DisabledGlossarySnapshot:
        if not self.enabled:
            return DisabledGlossarySnapshot()
        with self._lock:
            if self._snapshot is None:
                self._snapshot = self._load_snapshot()
            return self._snapshot

    def reload(self) -> GlossarySnapshot | DisabledGlossarySnapshot:
        if not self.enabled:
            return DisabledGlossarySnapshot()
        loaded = self._load_snapshot()
        with self._lock:
            self._snapshot = loaded
            self._metrics["glossary_reload_count"] += 1
            return loaded

    def metrics(self) -> dict[str, int | float | str | None]:
        with self._lock:
            calls = int(self._metrics["correction_calls"])
            latency = (
                float(self._metrics["correction_latency_total_ms"]) / calls
                if calls
                else 0.0
            )
            return {
                "glossary_terms_loaded": int(self._metrics["glossary_terms_loaded"]),
                "corrections_applied": int(self._metrics["corrections_applied"]),
                "segments_corrected": int(self._metrics["segments_corrected"]),
                "unmatched_aliases": int(self._metrics["unmatched_aliases"]),
                "correction_latency_ms": round(latency, 6),
                "glossary_reload_count": int(self._metrics["glossary_reload_count"]),
                "correction_conflicts": int(self._metrics["correction_conflicts"]),
                "glossary_version": getattr(self._snapshot, "version", None),
            }

    def _load_snapshot(self) -> GlossarySnapshot:
        raw = self.path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("terms"), list):
            raise ValueError("Glossary file must contain a terms array")
        terms = tuple(_parse_term(item) for item in payload["terms"])
        active = tuple(term for term in terms if term.active)
        context = _prompt_context(active, self.prompt_max_terms)
        version = hashlib.sha256(raw).hexdigest()[:16]
        with self._lock:
            self._metrics["glossary_terms_loaded"] = len(active)
        return GlossarySnapshot(version, active, context, self._record)

    def _record(
        self,
        corrections: int,
        corrected: bool,
        unmatched: int,
        conflicts: int,
        latency_ms: float,
    ) -> None:
        with self._lock:
            self._metrics["corrections_applied"] += corrections
            self._metrics["segments_corrected"] += int(corrected)
            self._metrics["unmatched_aliases"] += unmatched
            self._metrics["correction_conflicts"] += conflicts
            self._metrics["correction_latency_total_ms"] += latency_ms
            self._metrics["correction_calls"] += 1


def combine_prompt(base_prompt: str | None, glossary_context: str) -> str | None:
    parts = [part.strip() for part in (base_prompt or "", glossary_context) if part.strip()]
    return "\n\n".join(parts) or None


def _parse_term(value: object) -> GlossaryTerm:
    if not isinstance(value, dict):
        raise ValueError("Each glossary term must be an object")
    preferred = str(value.get("preferredSpelling", "")).strip()
    if not preferred:
        raise ValueError("Glossary preferredSpelling is required")
    aliases_value = value.get("aliases", [])
    if not isinstance(aliases_value, list):
        raise ValueError(f"Glossary aliases for {preferred} must be an array")
    aliases_list: list[str] = []
    alias_keys: set[str] = set()
    for alias_value in aliases_value:
        alias = str(alias_value).strip()
        if (
            alias
            and alias.casefold() != preferred.casefold()
            and alias.casefold() not in alias_keys
        ):
            aliases_list.append(alias)
            alias_keys.add(alias.casefold())
    aliases = tuple(aliases_list)
    priority = int(value.get("priority", 0))
    if not 0 <= priority <= 1000:
        raise ValueError(f"Glossary priority for {preferred} must be between 0 and 1000")
    return GlossaryTerm(
        preferred_spelling=preferred,
        aliases=aliases,
        do_not_change=bool(value.get("doNotChange", False)),
        category=str(value.get("category", "general")).strip() or "general",
        priority=priority,
        language=str(value.get("language", "*")).strip() or "*",
        active=bool(value.get("active", True)),
        preferred_translations=_parse_preferred_translations(
            value.get("preferredTranslations", {}), preferred
        ),
        do_not_translate=bool(value.get("doNotTranslate", False)),
    )


def _parse_preferred_translations(
    value: object,
    preferred: str,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        raise ValueError(f"Glossary preferredTranslations for {preferred} must be an object")
    translations: list[tuple[str, str]] = []
    for language, translated in value.items():
        language_value = str(language).strip()
        translated_value = str(translated).strip()
        if not language_value or not translated_value:
            raise ValueError(
                f"Glossary preferredTranslations for {preferred} cannot contain empty values"
            )
        translations.append((language_value, translated_value))
    return tuple(sorted(translations, key=lambda item: item[0].casefold()))


def _whole_term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![\w]){escaped}(?![\w])", flags=re.IGNORECASE | re.UNICODE)


def _language_matches(term_language: str, requested: str) -> bool:
    term = term_language.casefold()
    language = requested.casefold()
    if term in {"*", "all"} or language == "auto":
        return True
    return term == language or term.split("-", 1)[0] == language.split("-", 1)[0]


def _prompt_context(terms: tuple[GlossaryTerm, ...], limit: int) -> str:
    selected = sorted(terms, key=lambda term: (-term.priority, term.preferred_spelling.casefold()))[:limit]
    if not selected:
        return ""
    instructions = []
    for term in selected:
        aliases = f" (aliases: {', '.join(term.aliases)})" if term.aliases else ""
        verb = "Preserve exactly" if term.do_not_change else "Use preferred spelling"
        instructions.append(f"{verb}: {term.preferred_spelling}{aliases}.")
    return "Local terminology context:\n" + "\n".join(instructions)
