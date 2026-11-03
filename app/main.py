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
from app.media.one_time_rescue import rescue_reply_media
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
        SNAPSHOTS.pop(key, None)

    while len(SNAPSHOTS) > MAX_SNAPSHOTS:
        SNAPSHOTS.popitem(last=False)

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


def format_owner_settings(settings: dict) -> str:
    labels = {
        "capture_once": "استعادة الوسائط المؤقتة",
        "capture_any_media": "التقاط أي وسيط عند الرد",
        "rescue_photo": "الصور",
        "rescue_video": "الفيديو",
        "rescue_voice": "الرسائل الصوتية",
        "rescue_audio": "ملفات الصوت",
        "rescue_document": "الملفات",
        "rescue_animation": "صور GIF المتحركة",
        "rescue_video_note": "رسائل الفيديو الدائرية",
        "rescue_sticker": "الملصقات",
    }

    lines = ["⚙️ إعدادات البوت", ""]

    for key, label in labels.items():
        state = "مفعّل ✅" if settings[key] else "متوقف ⛔"
        lines.append(f"{label}: {state}")

    lines.extend([
        "",
        f"مدة الاحتفاظ: {settings['retention_hours']} ساعة",
        "",
        "أوامر التحكم:",
        "/setting capture_once on أو off",
        "/setting capture_any_media on أو off",
        "/setting rescue_photo on أو off",
        "/setting rescue_video on أو off",
        "/setting rescue_voice on أو off",
        "/setting rescue_audio on أو off",
        "/setting rescue_document on أو off",
        "/setting rescue_animation on أو off",
        "/setting rescue_video_note on أو off",
        "/setting rescue_sticker on أو off",
        "/retention 1 أو 6 أو 12 أو 24 أو 72 أو 168",
    ])

    return "\n".join(lines)


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

    connection = next(
        (
            item
            for item in CONNECTIONS.values()
            if item.get("enabled") and item.get("owner_id") == owner_id
        ),
        None,
    )

    if connection is None:
        return

    # The owner can manage only settings belonging to their own connection.
    command_parts = text_content.strip().split()
    command = command_parts[0].split("@", 1)[0].lower()

    if command in ("/start", "/settings"):
        await notify(chat_id, format_owner_settings(
            await get_owner_settings(owner_id)
        ))
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
    SNAPSHOTS[key] = {
        "text": message.get("text") or message.get("caption") or "",
        "media_type": media_type,
        "file_id": file_id,
        "file_unique_id": unique_id,
        "saved_at": time.monotonic(),
    }
    SNAPSHOTS.move_to_end(key)
    prune_memory()


def get_snapshot(connection_id: str, chat_id: int, message_id: int):
    prune_memory()
    key = (connection_id, chat_id, message_id)
    value = SNAPSHOTS.get(key)

    if value:
        SNAPSHOTS.move_to_end(key)

    return value


def forget_snapshot(connection_id: str, chat_id: int, message_id: int):
    SNAPSHOTS.pop((connection_id, chat_id, message_id), None)


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

    return await telegram(method, {
        "chat_id": owner_chat_id,
        field: file_id,
    })


def save_business_connection(data: dict):
    connection_id = data.get("id")
    user = data.get("user") or {}

    if not connection_id or not user.get("id") or not data.get("user_chat_id"):
        log.warning("Business connection update is incomplete")
        return

    if not data.get("is_enabled"):
        CONNECTIONS.pop(connection_id, None)
        log.info("Business connection disabled")
        return

    CONNECTIONS[connection_id] = {
        "owner_id": user["id"],
        "owner_chat_id": data["user_chat_id"],
        "enabled": True,
    }

    log.info("Business connection is active in RAM")


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

    # Owner reply: use the isolated media rescue module.
    if sender_id == owner_id:
        original = message.get("reply_to_message") or {}
        original_sender = (original.get("from") or {}).get("id")

        if original_sender != owner_id:
            result = await rescue_reply_media(
                message,
                owner_chat_id,
                send_saved_media,
                owner_settings,
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

    old = get_snapshot(connection_id, chat_id, message_id) if edited else None

    text_content = message.get("text") or message.get("caption") or ""
    media_type, file_id, _ = extract_media(message)

    remember_snapshot(connection_id, message)

    if edited:
        old_text = old["text"] if old else "(النسخة السابقة غير متوفرة في الذاكرة)"
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
            await send_saved_media(owner_chat_id, media_type, file_id)
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
        snapshot = get_snapshot(connection_id, chat_id, message_id)

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
                await send_saved_media(
                    owner_chat_id,
                    snapshot["media_type"],
                    snapshot["file_id"],
                )
            except Exception:
                log.error("Deleted media resend failed")

        forget_snapshot(connection_id, chat_id, message_id)


async def handle_update(update: dict):
    if "business_connection" in update:
        data = update["business_connection"]
        save_business_connection(data)
        await firestore_store.persist_business_connection(data)
    elif "business_message" in update:
        await process_business_message(update["business_message"])
    elif "edited_business_message" in update:
        await process_business_message(
            update["edited_business_message"],
            edited=True,
        )
    elif "deleted_business_messages" in update:
        await process_deleted_messages(update["deleted_business_messages"])
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
    while True:
        prune_memory()
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
