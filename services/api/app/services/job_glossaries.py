"""Explicitly selectable glossary catalogue for batch transcription jobs."""

from functools import lru_cache
from pathlib import Path

from .glossary import GlossaryManager, GlossarySnapshot

PROJECT_ROOT = Path(__file__).resolve().parents[4]
GLOSSARY_ROOT = PROJECT_ROOT / "config"


def list_job_glossaries() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in sorted(GLOSSARY_ROOT.glob("glossary.*.json")):
        glossary_id = path.name.removeprefix("glossary.").removesuffix(".json")
        entries.append({"id": glossary_id, "name": glossary_id.replace("-", " ").replace("_", " ").title()})
    return entries


def resolve_job_glossary(glossary_id: str) -> Path:
    available = {item["id"] for item in list_job_glossaries()}
    if glossary_id not in available:
        raise ValueError(f"Unknown glossary: {glossary_id}")
    return GLOSSARY_ROOT / f"glossary.{glossary_id}.json"


@lru_cache(maxsize=16)
def load_job_glossary(glossary_id: str) -> GlossarySnapshot:
    snapshot = GlossaryManager(resolve_job_glossary(glossary_id), enabled=True).snapshot()
    if not isinstance(snapshot, GlossarySnapshot):  # pragma: no cover - enabled above
        raise RuntimeError("Selected glossary could not be loaded")
    return snapshot
