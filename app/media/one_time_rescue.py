"""Extract and re-send media exposed by Telegram in reply_to_message."""

from typing import Any


MEDIA_FIELDS = (
    "photo",
    "video",
    "voice",
    "video_note",
    "audio",
    "document",
    "animation",
    "sticker",
)

ONE_TIME_FLAGS = (
    "view_once",
    "is_one_time",
    "one_time",
    "self_destruct",
    "self_destructing",
    "is_self_destruct",
    "ttl_seconds",
    "ttl_period",
)


def extract_reply_media(message: dict[str, Any] | None):
    if not isinstance(message, dict):
        return None

    for media_type in MEDIA_FIELDS:
        item = message.get(media_type)

        if not item:
            continue

        if media_type == "photo" and isinstance(item, list):
            item = item[-1] if item else None

        if not isinstance(item, dict):
            continue

        file_id = item.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            continue

        result = {
            "media_type": media_type,
            "file_id": file_id,
            "file_unique_id": item.get("file_unique_id"),
        }

        if item.get("file_size") is not None:
            result["file_size"] = item["file_size"]

        if item.get("file_name") is not None:
            result["file_name"] = item["file_name"]

        return result

    return None


def _flag_is_active(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value > 0
    return False


def is_one_time(message: dict[str, Any] | None) -> bool:
    """Best-effort detection; Telegram does not guarantee a stable marker."""
    if not isinstance(message, dict):
        return False

    candidates = [message]

    for media_type in MEDIA_FIELDS:
        item = message.get(media_type)

        if media_type == "photo" and isinstance(item, list):
            item = item[-1] if item else None

        if isinstance(item, dict):
            candidates.append(item)

    for candidate in candidates:
        for flag in ONE_TIME_FLAGS:
            if _flag_is_active(candidate.get(flag)):
                return True

    return False


async def rescue_reply_media(
    message: dict,
    owner_chat_id: int,
    send_media,
    settings: dict | None = None,
):
    """
    Re-send media only when Telegram exposed a usable file_id.

    When settings are supplied, capture_once is the master switch and
    capture_any_media allows media without a recognized one-time marker.
    """
    original = message.get("reply_to_message") or {}
    media = extract_reply_media(original)

    if not media:
        return {"ok": False, "reason": "media_unavailable"}

    if settings is not None:
        if not settings.get("capture_once", True):
            return {"ok": False, "reason": "rescue_disabled"}

        if not is_one_time(original) and not settings.get(
            "capture_any_media", False
        ):
            return {"ok": False, "reason": "not_one_time"}

        setting_name = f"rescue_{media['media_type']}"
        if settings.get(setting_name, True) is False:
            return {"ok": False, "reason": "media_type_disabled"}

    try:
        await send_media(
            owner_chat_id,
            media["media_type"],
            media["file_id"],
        )
    except Exception:
        return {
            "ok": False,
            "reason": "telegram_send_failed",
            "media_type": media["media_type"],
        }

    return {"ok": True, "media_type": media["media_type"]}
