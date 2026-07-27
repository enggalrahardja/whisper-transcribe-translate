from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from ..config import get_settings

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
CHUNK_SIZE = 1024 * 1024


def get_upload_directory() -> Path:
    directory = Path(get_settings().storage_root).resolve() / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


async def save_upload(upload: UploadFile) -> dict[str, str | int]:
    original_name = Path(upload.filename or "").name
    extension = Path(original_name).suffix.lower()
    media_type = ALLOWED_EXTENSIONS.get(extension)
    if media_type is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported media format",
        )

    stored_name = f"{uuid4().hex}{extension}"
    destination = get_upload_directory() / stored_name
    size = 0

    try:
        with destination.open("wb") as output:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
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

    return {
        "file_name": original_name,
        "stored_name": stored_name,
        "storage_path": str(destination),
        "file_size": size,
        "content_type": upload.content_type or "application/octet-stream",
        "media_type": media_type,
    }
