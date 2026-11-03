from typing import Any


SUPPORTED_MEDIA = (
    "photo",
    "video",
    "voice",
    "video_note",
    "audio",
    "document",
    "animation",
    "sticker",
)


def extract_reply_media(message: dict[str, Any] | None):
    """
    Extract media exposed by Telegram in a replied-to message.
    Does not bypass Telegram privacy or one-time media restrictions.
    """
    if not isinstance(message, dict):
        return None

    for media_type in SUPPORTED_MEDIA:
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

        return {
            "media_type": media_type,
            "file_id": file_id,
            "file_unique_id": item.get("file_unique_id"),
        }

    return None


async def rescue_reply_media(
    message: dict,
    owner_chat_id: int,
    send_media,
):
    """
    Re-send accessible media from reply_to_message.
    Returns a structured result; never claims recovery if Telegram
    did not expose a usable file_id.
    """
    original = message.get("reply_to_message") or {}
    media = extract_reply_media(original)

    if not media:
        return {
            "ok": False,
            "reason": "media_unavailable",
        }

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

    return {
        "ok": True,
        "media_type": media["media_type"],
    }
