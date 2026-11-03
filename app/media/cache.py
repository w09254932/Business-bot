"""Local media cache inspired by Vestige's CACHE_MEDIA implementation."""

import hashlib
import mimetypes
import os
import uuid
from pathlib import Path

import httpx


MEDIA_METHODS = {
    "photo": ("sendPhoto", "photo"),
    "video": ("sendVideo", "video"),
    "voice": ("sendVoice", "voice"),
    "video_note": ("sendVideoNote", "video_note"),
    "audio": ("sendAudio", "audio"),
    "document": ("sendDocument", "document"),
    "animation": ("sendAnimation", "animation"),
    "sticker": ("sendSticker", "sticker"),
}


def cache_enabled():
    return os.getenv("CACHE_MEDIA", "false").strip().lower() in (
        "true", "1", "yes", "on"
    )


def cache_directory():
    return Path(os.getenv("MEDIA_DIR", "data/media")).expanduser()


def max_file_bytes():
    try:
        mb = int(os.getenv("MAX_CACHE_FILE_MB", "20"))
    except ValueError:
        mb = 20
    return max(1, mb) * 1024 * 1024


def max_total_bytes():
    try:
        mb = int(os.getenv("MAX_CACHE_TOTAL_MB", "256"))
    except ValueError:
        mb = 256
    return max(1, mb) * 1024 * 1024


def media_metadata(message):
    if not isinstance(message, dict):
        return None

    for kind in MEDIA_METHODS:
        item = message.get(kind)

        if kind == "photo" and isinstance(item, list):
            item = item[-1] if item else None

        if not isinstance(item, dict):
            continue

        file_id = item.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            continue

        return {
            "media_type": kind,
            "file_id": file_id,
            "file_unique_id": item.get("file_unique_id"),
            "file_size": item.get("file_size"),
        }

    return None


def _directory_size(directory):
    if not directory.exists():
        return 0

    total = 0
    for item in directory.iterdir():
        try:
            if item.is_file() and not item.name.endswith(".part"):
                total += item.stat().st_size
        except OSError:
            continue

    return total


async def cache_telegram_media(
    token,
    file_id,
    file_unique_id=None,
    declared_size=None,
):
    """Download a Telegram file to the configured local cache directory."""
    if not cache_enabled() or not token or not file_id:
        return None

    directory = cache_directory()
    file_limit = max_file_bytes()
    total_limit = max_total_bytes()

    if declared_size is not None and declared_size > file_limit:
        return None

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    if _directory_size(directory) >= total_limit:
        return None

    api = f"https://api.telegram.org/bot{token}"

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{api}/getFile",
                json={"file_id": file_id},
            )

            try:
                payload = response.json()
            except ValueError:
                return None

            if response.status_code >= 400 or not payload.get("ok"):
                return None

            result = payload.get("result") or {}
            remote_path = result.get("file_path")
            remote_size = result.get("file_size")

            if not isinstance(remote_path, str) or not remote_path:
                return None

            if remote_size is not None and remote_size > file_limit:
                return None

            download_url = (
                f"https://api.telegram.org/file/bot{token}/{remote_path}"
            )
            download = await client.get(download_url)

            if download.status_code >= 400:
                return None

            content = download.content

        if len(content) > file_limit:
            return None

        if _directory_size(directory) + len(content) > total_limit:
            return None

        suffix = Path(remote_path).suffix.lower()
        if (
            not suffix
            or len(suffix) > 10
            or not suffix[1:].isalnum()
        ):
            suffix = ".bin"

        identity = file_unique_id or file_id
        prefix = hashlib.sha256(identity.encode()).hexdigest()[:16]
        filename = f"{prefix}_{uuid.uuid4().hex}{suffix}"

        target = directory / filename
        temporary = directory / f"{filename}.part"

        temporary.write_bytes(content)
        temporary.chmod(0o600)
        os.replace(temporary, target)

        try:
            directory.chmod(0o700)
        except OSError:
            pass

        return str(target)

    except Exception:
        return None


async def upload_cached_media(token, chat_id, media_type, file_path):
    """Upload a cached local file to Telegram using multipart/form-data."""
    if media_type not in MEDIA_METHODS:
        raise ValueError("Unsupported media type")

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError("Cached media file is unavailable")

    method, field = MEDIA_METHODS[media_type]
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    url = f"https://api.telegram.org/bot{token}/{method}"

    with path.open("rb") as file_handle:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                data={"chat_id": str(chat_id)},
                files={
                    field: (
                        path.name,
                        file_handle,
                        content_type,
                    )
                },
            )

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError(
            f"Telegram media upload returned HTTP {response.status_code}"
        )

    if response.status_code >= 400 or not payload.get("ok"):
        code = payload.get("error_code", response.status_code)
        description = payload.get("description", "Unknown Telegram API error")
        raise RuntimeError(
            f"Telegram API {method} upload failed: {code} - {description}"
        )

    return payload.get("result")


def delete_cached_media(file_path):
    """Delete only files contained inside the configured cache directory."""
    if not file_path:
        return False

    try:
        root = cache_directory().resolve()
        target = Path(file_path).resolve()

        if os.path.commonpath([str(root), str(target)]) != str(root):
            return False

        if not target.is_file():
            return False

        target.unlink()
        return True

    except (OSError, ValueError):
        return False
