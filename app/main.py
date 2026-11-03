import os
import hmac
import logging
import time
import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from app.media.one_time_rescue import is_one_time, rescue_reply_media
from app.media.cache import (
    cache_enabled,
    cache_telegram_media,
    delete_cached_media,
    media_metadata,
    upload_cached_media,
)
from app.database import firestore_store
from app.user_settings import (
    BOOLEAN_SETTINGS,
    DEFAULT_USER_SETTINGS,
    RETENTION_OPTIONS,
    normalize_user_settings,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# RAM-only configuration
SNAPSHOT_TTL = 6 * 60 * 60
MAX_SNAPSHOTS = 5000
MAX_PROCESSED_UPDATES = 10000

# Nothing here is written to disk.
CONNECTIONS = {}
SNAPSHOTS = OrderedDict()
PROCESSED_UPDATES = OrderedDict()
USER_SETTINGS = {}
MEMORY_CLEANUP_INTERVAL_SECONDS = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("business-bot")


def extract_media(message: dict):
    if not isinstance(message, dict):
        return None, None, None

    for kind in (
        "photo", "video", "voice", "video_note",
        "audio", "document", "animation", "sticker"
    ):
        item = message.get(kind)
        if not item:
            continue

        if kind == "photo" and isinstance(item, list):
            item = item[-1] if item else None

        if isinstance(item, dict):
            return (
                kind,
                item.get("file_id"),
                item.get("file_unique_id"),
            )

    return None, None, None


def prune_memory():
    now = time.monotonic()

    # Remove every expired snapshot, regardless of LRU access order.
    expired_keys = []

    for key, value in SNAPSHOTS.items():
        connection = CONNECTIONS.get(key[0]) or {}
        owner_id = connection.get("owner_id")
        if owner_id is not None and owner_id in USER_SETTINGS:
            settings = USER_SETTINGS[owner_id]
            ttl_seconds = int(settings["retention_hours"]) * 3600
        else:
            ttl_seconds = SNAPSHOT_TTL

        if now - value["saved_at"] > ttl_seconds:
            expired_keys.append(key)

    for key in expired_keys:
        old_snapshot = SNAPSHOTS.pop(key, None)
        if old_snapshot:
            delete_cached_media(old_snapshot.get("file_path"))

    while len(SNAPSHOTS) > MAX_SNAPSHOTS:
        _, old_snapshot = SNAPSHOTS.popitem(last=False)
        delete_cached_media(old_snapshot.get("file_path"))

    while len(PROCESSED_UPDATES) > MAX_PROCESSED_UPDATES:
        PROCESSED_UPDATES.popitem(last=False)



async def get_owner_settings(owner_id: int) -> dict:
    owner_id = int(owner_id)

    if owner_id in USER_SETTINGS:
        return USER_SETTINGS[owner_id]

    saved = await firestore_store.load_user_settings([owner_id])

    if saved is None:
        data = {}
    else:
        data = saved.get(str(owner_id), {})

    settings = normalize_user_settings(data)
    USER_SETTINGS[owner_id] = settings
    return settings


async def save_owner_settings(owner_id: int, settings: dict):
    owner_id = int(owner_id)
    safe_settings = normalize_user_settings(settings)

    await firestore_store.persist_user_settings(
        owner_id,
        safe_settings,
    )

    USER_SETTINGS[owner_id] = safe_settings
    prune_memory()


SETTING_LABELS = {
    "capture_once": "استعادة الوسائط المؤقتة",
    "capture_any_media": "التقاط الوسائط العادية عند الرد",
    "rescue_photo": "الصور",
    "rescue_video": "الفيديو",
    "rescue_voice": "الرسائل الصوتية",
    "rescue_audio": "ملفات الصوت",
    "rescue_document": "الملفات",
    "rescue_animation": "صور GIF المتحركة",
    "rescue_video_note": "رسائل الفيديو الدائرية",
    "rescue_sticker": "الملصقات",
}


def format_owner_settings(settings: dict) -> str:
    lines = [
        "⚙️ إعدادات البوت",
        "",
        "اضغط على الأزرار لتشغيل الميزات أو إيقافها.",
        "",
    ]

    for key, label in SETTING_LABELS.items():
        state = "مفعّل ✅" if settings[key] else "متوقف ⛔"
        lines.append(f"{label}: {state}")

    lines.extend([
        "",
        f"⏳ مدة الاحتفاظ الحالية: {settings['retention_hours']} ساعة",
    ])
    return "\n".join(lines)


def build_settings_keyboard(settings: dict):
    def toggle(key):
        enabled = settings[key]
        return {
            "text": (
                f"{'✅' if enabled else '⛔'} "
                f"{SETTING_LABELS[key]}"
            ),
            "callback_data": f"settings:toggle:{key}",
        }

    return {
        "inline_keyboard": [
            [toggle("capture_once")],
            [toggle("capture_any_media")],
            [toggle("rescue_photo"), toggle("rescue_video")],
            [toggle("rescue_voice"), toggle("rescue_audio")],
            [toggle("rescue_document"), toggle("rescue_animation")],
            [toggle("rescue_video_note"), toggle("rescue_sticker")],
            [{
                "text": f"⏳ مدة الاحتفاظ: {settings['retention_hours']} ساعة",
                "callback_data": "settings:retention",
            }],
            [{
                "text": "🔗 إدارة اتصال Telegram Business",
                "url": "tg://settings/business",
            }],
        ]
    }


def build_retention_keyboard(settings: dict):
    options = RETENTION_OPTIONS
    rows = []

    for index in range(0, len(options), 2):
        row = []
        for hours in options[index:index + 2]:
            selected = settings["retention_hours"] == hours
            row.append({
                "text": f"{'✅ ' if selected else ''}{hours} ساعة",
                "callback_data": f"settings:retention:{hours}",
            })
        rows.append(row)

    rows.append([{
        "text": "↩️ الرجوع للإعدادات",
        "callback_data": "settings:home",
    }])

    return {"inline_keyboard": rows}


def build_welcome_keyboard():
    return {
        "inline_keyboard": [[
            {
                "text": "🔗 توصيل حساب Telegram Business",
                "url": "tg://settings/business",
            }
        ]]
    }


async def send_welcome_menu(chat_id: int):
    lines = [
        "مرحبًا بك في بوت إدارة Telegram Business 🤖",
        "",
        "للبدء، اربط حساب Telegram Business الخاص بك.",
        "",
        "1. اضغط على زر توصيل الحساب.",
        "2. افتح قسم Chatbots داخل إعدادات الأعمال.",
        "3. اختر هذا البوت وحدد المحادثات والصلاحيات.",
        "4. وافق على التوصيل وارجع إلى هذه المحادثة.",
        "",
        "بعد وصول تأكيد Telegram سيعرض البوت إعدادات حسابك الخاصة.",
    ]

    return await telegram("sendMessage", {
        "chat_id": chat_id,
        "text": chr(10).join(lines),
        "reply_markup": build_welcome_keyboard(),
    })


async def process_private_message(message: dict):
    chat = message.get("chat") or {}
    sender = message.get("from") or {}

    if chat.get("type") != "private":
        return

    owner_id = sender.get("id")
    chat_id = chat.get("id")
    text_content = message.get("text") or ""

    if not owner_id or not chat_id or not text_content.startswith("/"):
        return

    # The owner can open the bot before connecting a Business account.
    command_parts = text_content.strip().split()
    command = command_parts[0].split("@", 1)[0].lower()

    connection = next(
        (
            item
            for item in CONNECTIONS.values()
            if item.get("enabled") and item.get("owner_id") == owner_id
        ),
        None,
    )

    if command in ("/start", "/settings"):
        if connection is None:
            await send_welcome_menu(chat_id)
        else:
            await send_settings_menu(
                chat_id,
                await get_owner_settings(owner_id),
            )
        return

    if connection is None:
        return

    if command == "/setting":
        if len(command_parts) != 3:
            await notify(
                chat_id,
                "صيغة الأمر: /setting اسم_الإعداد on أو off\n"
                "استخدم /settings لعرض أسماء الإعدادات."
            )
            return

        key = command_parts[1].strip()
        value = command_parts[2].lower()

        if key not in BOOLEAN_SETTINGS or value not in ("on", "off"):
            await notify(
                chat_id,
                "الإعداد غير معروف أو القيمة غير صحيحة. "
                "راجع /settings."
            )
            return

        settings = dict(await get_owner_settings(owner_id))
        settings[key] = value == "on"

        await save_owner_settings(owner_id, settings)
        await notify(
            chat_id,
            f"تم تحديث {key}: {'مفعّل ✅' if settings[key] else 'متوقف ⛔'}"
        )
        return

    if command == "/retention":
        if len(command_parts) != 2:
            await notify(
                chat_id,
                "استخدم: /retention 1 أو 6 أو 12 أو 24 أو 72 أو 168"
            )
            return

        try:
            hours = int(command_parts[1])
        except ValueError:
            hours = -1

        if hours not in RETENTION_OPTIONS:
            await notify(
                chat_id,
                "المدة غير صحيحة. الخيارات: 1، 6، 12، 24، 72، 168 ساعة."
            )
            return

        settings = dict(await get_owner_settings(owner_id))
        settings["retention_hours"] = hours

        await save_owner_settings(owner_id, settings)
        await notify(
            chat_id,
            f"تم ضبط مدة الاحتفاظ على {hours} ساعة. "
            "سيتم تنظيف النسخ المنتهية تلقائياً."
        )
        return


def remember_update(update_id: int) -> bool:
    prune_memory()

    if update_id in PROCESSED_UPDATES:
        return False

    PROCESSED_UPDATES[update_id] = time.monotonic()
    prune_memory()
    return True


def remember_snapshot(connection_id: str, message: dict):
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")

    if chat_id is None or message_id is None:
        return

    media_type, file_id, unique_id = extract_media(message)

    # Keep only the minimum fields needed for temporary recovery.
    key = (connection_id, chat_id, message_id)
    previous = SNAPSHOTS.get(key)
    if previous:
        delete_cached_media(previous.get("file_path"))

    SNAPSHOTS[key] = {
        "text": message.get("text") or message.get("caption") or "",
        "media_type": media_type,
        "file_id": file_id,
        "file_unique_id": unique_id,
        "file_path": None,
        "saved_at": time.monotonic(),
    }
    SNAPSHOTS.move_to_end(key)
    prune_memory()
    return SNAPSHOTS.get(key)


def get_snapshot(connection_id: str, chat_id: int, message_id: int):
    prune_memory()
    key = (connection_id, chat_id, message_id)
    value = SNAPSHOTS.get(key)

    if value:
        SNAPSHOTS.move_to_end(key)

    return value


def forget_snapshot(connection_id: str, chat_id: int, message_id: int):
    snapshot = SNAPSHOTS.pop((connection_id, chat_id, message_id), None)
    if snapshot:
        delete_cached_media(snapshot.get("file_path"))



async def get_snapshot_with_firestore(
    connection_id: str,
    chat_id: int,
    message_id: int,
    connection: dict,
):
    # RAM is the fast path.
    snapshot = get_snapshot(connection_id, chat_id, message_id)
    if snapshot:
        return snapshot

    # Firestore is the persistent fallback.
    saved = await firestore_store.load_message_snapshot(
        connection["owner_id"],
        connection_id,
        chat_id,
        message_id,
    )

    if not saved:
        return None

    saved_at = int(saved.get("saved_at_unix") or time.time())
    age = max(0, time.time() - saved_at)

    snapshot = {
        "text": saved.get("text") or "",
        "media_type": saved.get("media_type"),
        "file_id": saved.get("file_id"),
        "file_unique_id": saved.get("file_unique_id"),
        "file_path": None,
        "saved_at": time.monotonic() - age,
    }

    key = (connection_id, chat_id, message_id)
    SNAPSHOTS[key] = snapshot
    SNAPSHOTS.move_to_end(key)
    prune_memory()

    return SNAPSHOTS.get(key)


async def persist_message_snapshot(
    owner_id: int,
    connection_id: str,
    message: dict,
    snapshot: dict,
):
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")

    if chat_id is None or message_id is None:
        return

    await firestore_store.persist_message_snapshot(
        owner_id,
        connection_id,
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "sender_id": (message.get("from") or {}).get("id"),
            "text": snapshot.get("text") or "",
            "media_type": snapshot.get("media_type"),
            "file_id": snapshot.get("file_id"),
            "file_unique_id": snapshot.get("file_unique_id"),
            "saved_at_unix": int(time.time()),
        },
    )


async def telegram(method: str, payload: dict):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")

    async with httpx.AsyncClient(timeout=35) as client:
        response = await client.post(f"{API}/{method}", json=payload)

        try:
            result = response.json()
        except ValueError:
            raise RuntimeError(
                f"Telegram API {method} returned HTTP {response.status_code} "
                "with an unreadable response"
            )

    if not result.get("ok") or response.status_code >= 400:
        description = result.get("description", "Unknown Telegram API error")
        error_code = result.get("error_code", response.status_code)
        raise RuntimeError(
            f"Telegram API {method} failed: "
            f"{error_code} - {description}"
        )

    return result.get("result")


async def notify(owner_chat_id: int, text: str):
    try:
        await telegram("sendMessage", {
            "chat_id": owner_chat_id,
            "text": text[:4000],
        })
    except Exception:
        log.error("Notification delivery failed")


async def send_saved_media(
    owner_chat_id: int,
    media_type: str,
    file_id: str,
    file_path: str | None = None,
):
    methods = {
        "photo": ("sendPhoto", "photo"),
        "video": ("sendVideo", "video"),
        "voice": ("sendVoice", "voice"),
        "video_note": ("sendVideoNote", "video_note"),
        "audio": ("sendAudio", "audio"),
        "document": ("sendDocument", "document"),
        "animation": ("sendAnimation", "animation"),
        "sticker": ("sendSticker", "sticker"),
    }

    if media_type not in methods:
        raise ValueError("Unsupported media type")

    method, field = methods[media_type]

    try:
        return await telegram(method, {
            "chat_id": owner_chat_id,
            field: file_id,
        })
    except Exception:
        if file_path and os.path.isfile(file_path):
            return await upload_cached_media(
                BOT_TOKEN,
                owner_chat_id,
                media_type,
                file_path,
            )
        raise


async def send_settings_menu(
    chat_id: int,
    settings: dict,
    message_id: int | None = None,
    retention_menu: bool = False,
):
    if retention_menu:
        text = (
            "⏳ اختر مدة الاحتفاظ بنسخ الرسائل.\n"
            f"المدة الحالية: {settings['retention_hours']} ساعة"
        )
        keyboard = build_retention_keyboard(settings)
    else:
        text = format_owner_settings(settings)
        keyboard = build_settings_keyboard(settings)

    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": keyboard,
    }

    if message_id is not None:
        payload["message_id"] = message_id
        return await telegram("editMessageText", payload)

    return await telegram("sendMessage", payload)


async def process_callback_query(callback: dict):
    callback_id = callback.get("id")
    actor_id = (callback.get("from") or {}).get("id")
    data = callback.get("data") or ""
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")

    connection = next(
        (
            item for item in CONNECTIONS.values()
            if item.get("enabled") and item.get("owner_id") == actor_id
        ),
        None,
    )

    if not connection:
        if callback_id:
            await telegram("answerCallbackQuery", {
                "callback_query_id": callback_id,
                "text": "هذه الأزرار ليست مخصصة لحسابك.",
                "show_alert": True,
            })
        return

    if callback_id:
        # Acknowledge the button press to stop Telegram's loading indicator.
        await telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
        })

    if chat_id is None or message_id is None:
        return

    settings = dict(await get_owner_settings(actor_id))

    if data == "settings:home":
        await send_settings_menu(chat_id, settings, message_id)
        return

    if data == "settings:retention":
        await send_settings_menu(
            chat_id, settings, message_id, retention_menu=True
        )
        return

    if data.startswith("settings:toggle:"):
        key = data.split(":", 2)[2]
        if key not in BOOLEAN_SETTINGS:
            return

        settings[key] = not settings[key]
        await save_owner_settings(actor_id, settings)
        await send_settings_menu(chat_id, settings, message_id)
        return

    if data.startswith("settings:retention:"):
        try:
            hours = int(data.split(":", 2)[2])
        except ValueError:
            return

        if hours not in RETENTION_OPTIONS:
            return

        settings["retention_hours"] = hours
        await save_owner_settings(actor_id, settings)
        await send_settings_menu(chat_id, settings, message_id)
        return


def clear_connection_snapshots(connection_id: str):
    for key in list(SNAPSHOTS):
        if key[0] == connection_id:
            snapshot = SNAPSHOTS.pop(key, None)
            if snapshot:
                delete_cached_media(snapshot.get("file_path"))


def save_business_connection(data: dict):
    connection_id = data.get("id")
    user = data.get("user") or {}
    owner_id = user.get("id")
    owner_chat_id = data.get("user_chat_id")

    if not connection_id or not owner_id or not owner_chat_id:
        log.warning("Business connection update is incomplete")
        return None

    if not data.get("is_enabled"):
        previous = CONNECTIONS.pop(connection_id, None)
        clear_connection_snapshots(connection_id)
        log.info("Business connection disabled")
        return {
            "owner_id": int(owner_id),
            "owner_chat_id": int(owner_chat_id),
            "enabled": False,
            "previous": previous,
        }

    rights = data.get("rights") or {}
    connection = {
        "owner_id": int(owner_id),
        "owner_chat_id": int(owner_chat_id),
        "enabled": True,
        "rights": {
            key: value
            for key, value in rights.items()
            if isinstance(value, bool)
        },
        "business_user": {
            key: user[key]
            for key in ("id", "first_name", "last_name", "username")
            if key in user
        },
        "connected_at": data.get("date"),
    }

    CONNECTIONS[connection_id] = connection
    log.info("Business connection activated for owner %s", owner_id)
    return connection


async def process_business_message(message: dict, edited: bool = False):
    connection_id = message.get("business_connection_id")
    if not connection_id:
        return

    connection = CONNECTIONS.get(connection_id)
    if not connection or not connection["enabled"]:
        log.warning("Business connection is not available in RAM")
        return

    sender_id = (message.get("from") or {}).get("id")
    owner_id = connection["owner_id"]
    owner_chat_id = connection["owner_chat_id"]
    owner_settings = await get_owner_settings(owner_id)

    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")

    # Only replies starting with Fatha trigger media rescue.
    if sender_id == owner_id:
        reply_content = message.get("text") or message.get("caption") or ""
        if not reply_content.startswith(chr(0x064e)):
            return

        original = message.get("reply_to_message") or {}
        original_sender = (original.get("from") or {}).get("id")

        if original_sender != owner_id:
            # Ignore normal text replies; rescue is only for supported media.
            supported_media = (
                "photo", "video", "voice", "video_note",
                "audio", "document", "animation", "sticker",
            )
            if not any(original.get(field) for field in supported_media):
                return

            original_chat_id = (original.get("chat") or {}).get("id")
            original_message_id = original.get("message_id")
            original_snapshot = None

            if original_chat_id is not None and original_message_id is not None:
                original_snapshot = await get_snapshot_with_firestore(
                    connection_id,
                    original_chat_id,
                    original_message_id,
                    connection,
                )

            async def send_reply_media(chat_id, media_type, file_id):
                cached_path = None

                if (
                    original_snapshot
                    and original_snapshot.get("media_type") == media_type
                    and original_snapshot.get("file_id") == file_id
                ):
                    cached_path = original_snapshot.get("file_path")

                # If the original snapshot has no cached file, attempt
                # getFile while Telegram still exposes the reply media.
                if not cached_path and cache_enabled():
                    metadata = media_metadata(original) or {}
                    cached_path = await cache_telegram_media(
                        BOT_TOKEN,
                        file_id,
                        metadata.get("file_unique_id"),
                        metadata.get("file_size"),
                    )

                    if cached_path and original_snapshot is not None:
                        original_snapshot["file_path"] = cached_path

                if cached_path:
                    return await send_saved_media(
                        chat_id, media_type, file_id, cached_path
                    )

                return await send_saved_media(
                    chat_id, media_type, file_id
                )

            # The Fatha prefix is explicit permission to rescue
            # this specific reply, including ordinary media.
            rescue_settings = dict(owner_settings)
            rescue_settings["capture_any_media"] = True

            result = await rescue_reply_media(
                message,
                owner_chat_id,
                send_reply_media,
                rescue_settings,
            )

            if result["ok"]:
                await notify(
                    owner_chat_id,
                    "تم العثور على الوسيط في الرسالة التي رددت عليها "
                    "وإعادة إرساله بنجاح."
                )
            elif result["reason"] == "media_unavailable":
                await notify(
                    owner_chat_id,
                    "لم تصل بيانات وسيط قابلة لإعادة الإرسال من Telegram "
                    "في الرسالة التي رددت عليها."
                )
            elif result["reason"] in (
                "not_one_time",
                "rescue_disabled",
                "media_type_disabled",
            ):
                return
            else:
                telegram_error = result.get(
                    "telegram_error",
                    "سبب الرفض غير متوفر",
                )
                await notify(
                    owner_chat_id,
                    "وصلت بيانات الوسيط، لكن Telegram رفض إعادة إرساله.\\n"
                    f"تفاصيل الرفض: {telegram_error}"
                )
        return

    if chat_id is None or message_id is None:
        return

    old = (
        await get_snapshot_with_firestore(
            connection_id, chat_id, message_id, connection
        )
        if edited else None
    )

    text_content = message.get("text") or message.get("caption") or ""
    media_type, file_id, _ = extract_media(message)

    snapshot = remember_snapshot(connection_id, message)

    # Cache ordinary media on arrival. Known one-time media is excluded.
    if (
        snapshot
        and media_type
        and file_id
        and cache_enabled()
    ):
        metadata = media_metadata(message) or {}

        try:
            cached_path = await cache_telegram_media(
                BOT_TOKEN,
                file_id,
                metadata.get("file_unique_id"),
                metadata.get("file_size"),
            )
            if cached_path:
                snapshot["file_path"] = cached_path
        except Exception:
            log.warning("Media cache failed")

    if snapshot is not None:
        await persist_message_snapshot(
            owner_id,
            connection_id,
            message,
            snapshot,
        )

    if edited:
        old_text = old["text"] if old else "(النسخة السابقة غير متوفرة)"
        await notify(
            owner_chat_id,
            "✏️ تم تعديل رسالة.\n"
            f"النص السابق: {old_text[:1200]}\n"
            f"النص الحالي: {text_content[:1200] or '(لا يوجد نص)'}"
        )
    else:
        await notify(
            owner_chat_id,
            "📩 رسالة جديدة.\n"
            f"المحتوى: {text_content[:2500] or '(رسالة وسائط)'}\n"
            f"نوع الوسيط: {media_type or 'لا يوجد'}"
        )

    # Forward accessible media to the owner without downloading or archiving it.
    if media_type and file_id:
        try:
            cached_path = snapshot.get("file_path") if snapshot else None

            if cached_path:
                await send_saved_media(
                    owner_chat_id, media_type, file_id, cached_path
                )
            else:
                await send_saved_media(
                    owner_chat_id, media_type, file_id
                )
        except Exception:
            log.error("Incoming media forwarding failed")


async def process_deleted_messages(data: dict):
    connection_id = data.get("business_connection_id")
    chat_id = (data.get("chat") or {}).get("id")
    message_ids = data.get("message_ids") or []

    connection = CONNECTIONS.get(connection_id)
    if not connection or chat_id is None:
        log.warning("Deletion received without an in-memory connection")
        return

    owner_chat_id = connection["owner_chat_id"]

    for message_id in message_ids:
        snapshot = await get_snapshot_with_firestore(
            connection_id,
            chat_id,
            message_id,
            connection,
        )

        if not snapshot:
            await notify(
                owner_chat_id,
                "🗑️ تم حذف رسالة، لكن نسختها المؤقتة غير متوفرة. "
                "قد تكون الذاكرة فُقدت بعد إعادة تشغيل الخدمة أو انتهت مدة الاحتفاظ."
            )
            continue

        await notify(
            owner_chat_id,
            "🗑️ تم حذف رسالة.\n"
            f"النص المحفوظ مؤقتًا: {snapshot['text'][:2500] or '(لا يوجد نص)'}"
        )

        if snapshot["media_type"] and snapshot["file_id"]:
            try:
                cached_path = snapshot.get("file_path")

                if cached_path:
                    await send_saved_media(
                        owner_chat_id,
                        snapshot["media_type"],
                        snapshot["file_id"],
                        cached_path,
                    )
                else:
                    await send_saved_media(
                        owner_chat_id,
                        snapshot["media_type"],
                        snapshot["file_id"],
                    )
            except Exception:
                log.error("Deleted media resend failed")

        await firestore_store.delete_message_snapshot(
            connection["owner_id"],
            connection_id,
            chat_id,
            message_id,
        )
        forget_snapshot(connection_id, chat_id, message_id)


async def handle_update(update: dict):
    if "business_connection" in update:
        data = update["business_connection"]

        # Persist the connection update before making it active in RAM.
        await firestore_store.persist_business_connection(data)
        connection = save_business_connection(data)

        if connection:
            owner_id = connection["owner_id"]
            owner_chat_id = connection["owner_chat_id"]

            if connection["enabled"]:
                settings = await get_owner_settings(owner_id)
                await firestore_store.persist_user_settings(
                    owner_id,
                    settings,
                )
                await notify(
                    owner_chat_id,
                    "✅ تم توصيل حساب Telegram Business بنجاح. "
                    "هذه إعداداتك الخاصة.",
                )
                await send_settings_menu(owner_chat_id, settings)
            else:
                await notify(
                    owner_chat_id,
                    "🔌 تم فصل اتصال Telegram Business. "
                    "أوقفت خدمات الحساب. يمكنك إعادة توصيله من القائمة.",
                )
                await send_welcome_menu(owner_chat_id)

    elif "business_message" in update:
        await process_business_message(update["business_message"])
    elif "edited_business_message" in update:
        await process_business_message(
            update["edited_business_message"],
            edited=True,
        )
    elif "deleted_business_messages" in update:
        await process_deleted_messages(update["deleted_business_messages"])
    elif "callback_query" in update:
        await process_callback_query(update["callback_query"])
    elif "message" in update:
        await process_private_message(update["message"])


async def register_webhook():
    if not BOT_TOKEN or not WEBHOOK_SECRET:
        log.warning("Webhook registration skipped: missing environment variables")
        return

    base_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base_url:
        log.warning("RENDER_EXTERNAL_URL is missing")
        return

    try:
        await telegram("setWebhook", {
            "url": f"{base_url}/telegram/webhook",
            "secret_token": WEBHOOK_SECRET,
            "allowed_updates": [
                "message",
                "callback_query",
                "business_connection",
                "business_message",
                "edited_business_message",
                "deleted_business_messages",
            ],
            "drop_pending_updates": False,
        })
        log.info("Telegram webhook registered")
    except Exception:
        log.error("Webhook registration failed")


async def memory_janitor():
    last_firestore_cleanup = 0.0

    while True:
        prune_memory()

        now = time.monotonic()

        if (
            firestore_store.is_configured()
            and now - last_firestore_cleanup >= 3600
        ):
            visited = set()

            for connection_id, connection in list(CONNECTIONS.items()):
                owner_id = connection.get("owner_id")

                if not owner_id or (owner_id, connection_id) in visited:
                    continue

                visited.add((owner_id, connection_id))

                settings = USER_SETTINGS.get(
                    owner_id,
                    DEFAULT_USER_SETTINGS,
                )

                try:
                    await firestore_store.prune_message_snapshots(
                        owner_id,
                        connection_id,
                        settings.get("retention_hours", 6),
                    )
                except Exception:
                    log.error("Persistent snapshot cleanup failed")

            last_firestore_cleanup = now

        await asyncio.sleep(MEMORY_CLEANUP_INTERVAL_SECONDS)



@asynccontextmanager
async def lifespan(app: FastAPI):
    saved_connections = await firestore_store.load_connections()

    if saved_connections is not None:
        CONNECTIONS.clear()
        CONNECTIONS.update(saved_connections)
        log.info("Business connections restored from Firestore")

        owner_ids = {
            item["owner_id"]
            for item in CONNECTIONS.values()
            if item.get("enabled")
        }

        saved_settings = await firestore_store.load_user_settings(owner_ids)

        if saved_settings is not None:
            USER_SETTINGS.clear()
            for owner_id in owner_ids:
                USER_SETTINGS[int(owner_id)] = normalize_user_settings(
                    saved_settings.get(str(owner_id), {})
                )

        log.info("Owner settings restored")
    else:
        log.info("Storage mode: RAM only")

    janitor_task = asyncio.create_task(memory_janitor())

    await register_webhook()

    try:
        yield
    finally:
        janitor_task.cancel()
        try:
            await janitor_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Telegram Business Monitor - RAM PoC",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "service": "Telegram Business Monitor",
        "storage": firestore_store.storage_mode(),
        "status": "running",
    }


@app.get("/health")
async def health():
    prune_memory()
    return {
        "ok": True,
        "storage": firestore_store.storage_mode(),
        "active_connections": len(CONNECTIONS),
        "temporary_snapshots": len(SNAPSHOTS),
    }


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    supplied_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )

    if not WEBHOOK_SECRET or not hmac.compare_digest(
        supplied_secret, WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    update = await request.json()
    update_id = update.get("update_id")

    if update_id is None:
        raise HTTPException(status_code=400, detail="Missing update_id")

    if not remember_update(update_id):
        return {"ok": True, "duplicate": True}

    try:
        await handle_update(update)
    except Exception:
        PROCESSED_UPDATES.pop(update_id, None)
        log.error("Update processing failed")
        raise HTTPException(status_code=500, detail="Update processing failed")

    return {"ok": True, "processed": True}
