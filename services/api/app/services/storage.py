from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from ..config import get_settings
from .application_settings import get_application_settings

ALLOWED_EXTENSIONS = {
    ".wav": "audio",
    ".mp3": "audio",
    ".ogg": "audio",
    ".flac": "audio",
    ".m4a": "audio",
    ".mp4": "video",
    ".mov": "video",
    ".wmv": "video",
    ".avi": "video",
    ".mkv": "video",
}
CONTENT_TYPES = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".flac": "audio/flac",
    ".m4a": "audio/mp4", ".mp4": "video/mp4", ".mov": "video/quicktime", ".wmv": "video/x-ms-wmv",
    ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
}
CHUNK_SIZE = 1024 * 1024


def resolve_storage_file(path_value: str | Path, *, must_exist: bool = True) -> Path:
    storage_root = Path(get_settings().storage_root).resolve()
    path = Path(path_value).resolve()
    if not path.is_relative_to(storage_root):
        raise ValueError("Media path is outside the configured storage root")
    if must_exist and not path.is_file():
        raise FileNotFoundError(path)
    return path


def _safe_original_name(filename: str | None) -> str:
    name = Path((filename or "").replace("\\", "/")).name.strip()
    name = "".join(character for character in name if character >= " " and character != "\x7f")
    if not name or len(name) > 255:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid upload filename")
    return name


def _matches_media_signature(extension: str, header: bytes) -> bool:
    if extension == ".wav":
        return header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if extension == ".avi":
        return header.startswith(b"RIFF") and header[8:12] == b"AVI "
    if extension == ".mp3":
        return header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)
    if extension == ".ogg":
        return header.startswith(b"OggS")
    if extension == ".flac":
        return header.startswith(b"fLaC")
    if extension in {".m4a", ".mp4", ".mov"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if extension == ".wmv":
        return header.startswith(bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"))
    if extension == ".mkv":
        return header.startswith(bytes.fromhex("1a45dfa3"))
    return False


def get_upload_directory() -> Path:
    directory = Path(get_settings().storage_root).resolve() / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


async def save_upload(upload: UploadFile) -> dict[str, str | int]:
    runtime_settings = get_application_settings()
    storage_settings = runtime_settings.storage_retention
    original_name = _safe_original_name(upload.filename)
    extension = Path(original_name).suffix.lower()
    media_type = ALLOWED_EXTENSIONS.get(extension)
    if media_type is None or extension not in storage_settings.allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported media format",
        )

    stored_name = f"{uuid4().hex}{extension}"
    destination = get_upload_directory() / stored_name
    size = 0
    header = bytearray()
    maximum_size = storage_settings.upload_max_size_mb * 1024 * 1024

    try:
        with destination.open("wb") as output:
            while chunk := await upload.read(CHUNK_SIZE):
                if len(header) < 64:
                    header.extend(chunk[:64 - len(header)])
                size += len(chunk)
                if size > maximum_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Upload exceeds the {storage_settings.upload_max_size_mb} MB limit",
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )
    if not _matches_media_signature(extension, bytes(header)):
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File content does not match the selected media format",
        )

    return {
        "file_name": original_name,
        "stored_name": stored_name,
        "storage_path": str(destination),
        "file_size": size,
        "content_type": CONTENT_TYPES[extension],
        "media_type": media_type,
    }
