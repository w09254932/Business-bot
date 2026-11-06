import html
import os
from datetime import datetime, timezone, timedelta
from typing import Awaitable, Callable, Optional


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


def get_archive_channel() -> Optional[str]:
    channel = os.getenv("ARCHIVE_CHANNEL_ID", "").strip()
    return channel or None


def get_user_name(user: dict) -> str:
    if not isinstance(user, dict):
        return "غير متوفر"

    parts = [
        str(value).strip()
        for value in (user.get("first_name"), user.get("last_name"))
        if value is not None and str(value).strip()
    ]
    name = " ".join(parts)
    return name or str(user.get("title") or "غير معروف")


def get_user_link(user: dict) -> str:
    user = user if isinstance(user, dict) else {}
    username = user.get("username")
    user_id = user.get("id")
    name = html.escape(get_user_name(user))

    if username:
        safe_username = html.escape(str(username), quote=True)
        return f'<a href="https://t.me/{safe_username}">@{safe_username}</a>'

    if user_id is not None:
        return f'<a href="tg://user?id={int(user_id)}">{name}</a>'

    return name


def format_archive_date(message: dict) -> str:
    timestamp = message.get("date") if isinstance(message, dict) else None
    if timestamp is None:
        return "غير متوفر"

    try:
        return datetime.fromtimestamp(
            int(timestamp), timezone.utc
        ).astimezone(
            timezone(timedelta(hours=3))
        ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return "غير متوفر"


def format_archive_text(
    message: dict,
    owner_id: int,
    direction: str,
    event: str = "new",
) -> str:
    direction_labels = {
        "incoming": "📥 واردة",
        "outgoing": "📤 صادرة",
    }
    event_labels = {
        "new": "رسالة جديدة",
        "edited": "تعديل رسالة",
    }

    if direction not in direction_labels:
        raise ValueError("direction must be 'incoming' or 'outgoing'")

    event_label = event_labels.get(event, "سجل رسالة")
    sender = get_user_link(message.get("from") or {})
    chat = message.get("chat") or {}
    private_name = " ".join(str(value).strip() for value in (chat.get("first_name"), chat.get("last_name")) if value and str(value).strip())
    chat_name = (chat.get("title") or ("@" + str(chat["username"]) if chat.get("username") else None) or private_name or {"private": "محادثة خاصة", "group": "مجموعة", "supergroup": "مجموعة فائقة", "channel": "قناة"}.get(chat.get("type"), "غير معروف"))
    chat_name = html.escape(str(chat_name))
    chat_id = chat.get("id", "غير متوفر")
    message_id = message.get("message_id", "غير متوفر")
    content = message.get("text") or message.get("caption") or ""

    lines = [
        f"<b>{event_label} · {direction_labels[direction]}</b>",
        f"<b>صاحب الحساب:</b> <code>{int(owner_id)}</code>",
        f"<b>المرسل:</b> {sender}",
        f"<b>المحادثة:</b> {chat_name}",
        f"<b>معرّف المحادثة:</b> <code>{html.escape(str(chat_id))}</code>",
        f"<b>معرّف الرسالة:</b> <code>{html.escape(str(message_id))}</code>",
        f"<b>التاريخ:</b> {format_archive_date(message)}",
    ]

    if content:
        prefix = "\n".join(lines + ["", "<b>المحتوى:</b>"])
        max_length = max(0, 3900 - len(prefix) - 1)
        escaped_content = html.escape(str(content))

        if len(escaped_content) > max_length:
            clipped = escaped_content[:max(0, max_length - 1)]
            last_amp = clipped.rfind("&")
            last_semicolon = clipped.rfind(";")
            if last_amp > last_semicolon:
                clipped = clipped[:last_amp]
            escaped_content = clipped + "…"

        lines.extend(["", "<b>المحتوى:</b>", escaped_content])
    elif not any(message.get(kind) for kind in MEDIA_METHODS):
        lines.extend(["", "<i>(لا يوجد نص أو مرفق معروف)</i>"])

    return "\n".join(lines)


async def publish_archive_message(
    message: dict,
    owner_id: int,
    direction: str,
    telegram_call: Callable[[str, dict], Awaitable[dict]],
    media_type: Optional[str] = None,
    file_id: Optional[str] = None,
    event: str = "new",
    reply_to_message_id: Optional[int] = None,
) -> Optional[dict]:
    """Publish a message header and, when present, its Telegram media file_id."""
    archive_channel = get_archive_channel()
    if not archive_channel:
        return None

    payload = {
        "chat_id": archive_channel,
        "text": format_archive_text(message, owner_id, direction, event),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to_message_id is not None:
        payload["reply_parameters"] = {"message_id": int(reply_to_message_id)}

    header_result = await telegram_call("sendMessage", payload)
    if not isinstance(header_result, dict) or header_result.get("message_id") is None:
        raise RuntimeError("Telegram archive header response has no message_id")

    archive_message_id = int(header_result["message_id"])
    archive_chat_id = (header_result.get("chat") or {}).get("id", archive_channel)
    media_message_id = None
    media_status = "not_applicable"
    media_error = None

    if media_type and file_id:
        method_and_field = MEDIA_METHODS.get(media_type)
        if method_and_field is None:
            media_status = "unsupported"
        else:
            method, field = method_and_field
            try:
                media_result = await telegram_call(
                    method,
                    {
                        "chat_id": archive_channel,
                        field: file_id,
                        "reply_parameters": {
                            "message_id": archive_message_id
                        },
                    },
                )
                if isinstance(media_result, dict) and media_result.get("message_id") is not None:
                    media_message_id = int(media_result["message_id"])
                    media_status = "sent"
                else:
                    media_status = "failed"
                    media_error = "Telegram media response has no message_id"
            except Exception as exc:
                media_status = "failed"
                media_error = str(exc)[:500]

    return {
        "archive_chat_id": archive_chat_id,
        "archive_message_id": archive_message_id,
        "archive_media_message_id": media_message_id,
        "media_status": media_status,
        "media_error": media_error,
        "direction": direction,
        "event": event,
    }


async def publish_archive_notice(
    text: str,
    telegram_call: Callable[[str, dict], Awaitable[dict]],
    reply_to_message_id: Optional[int] = None,
) -> Optional[dict]:
    """Publish an archive notice, optionally as a reply to an archived message."""
    archive_channel = get_archive_channel()
    if not archive_channel:
        return None

    payload = {
        "chat_id": archive_channel,
        "text": str(text)[:4000],
    }
    if reply_to_message_id is not None:
        payload["reply_parameters"] = {"message_id": int(reply_to_message_id)}

    result = await telegram_call("sendMessage", payload)
    if not isinstance(result, dict) or result.get("message_id") is None:
        raise RuntimeError("Telegram archive notice response has no message_id")

    return {
        "archive_chat_id": (result.get("chat") or {}).get("id", archive_channel),
        "archive_message_id": int(result["message_id"]),
    }
