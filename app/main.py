import os
import hmac
import logging
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException

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

    while SNAPSHOTS:
        key, value = next(iter(SNAPSHOTS.items()))
        if now - value["saved_at"] <= SNAPSHOT_TTL:
            break
        SNAPSHOTS.pop(key, None)

    while len(SNAPSHOTS) > MAX_SNAPSHOTS:
        SNAPSHOTS.popitem(last=False)

    while len(PROCESSED_UPDATES) > MAX_PROCESSED_UPDATES:
        PROCESSED_UPDATES.popitem(last=False)


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
        response.raise_for_status()
        result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API {method} failed")

    return result.get("result")


async def notify(owner_chat_id: int, text: str):
    try:
        await telegram("sendMessage", {
            "chat_id": owner_chat_id,
            "text": text[:4000],
        })
    except Exception:
        log.exception("Notification delivery failed")


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

    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")

    # Owner reply: try to resend media only if Telegram exposed it.
    if sender_id == owner_id:
        original = message.get("reply_to_message") or {}
        original_sender = (original.get("from") or {}).get("id")
        media_type, file_id, _ = extract_media(original)

        if (
            media_type
            and file_id
            and original_sender != owner_id
        ):
            try:
                await send_saved_media(owner_chat_id, media_type, file_id)
                await notify(
                    owner_chat_id,
                    "وصلت وسائط من الرسالة التي رددت عليها، "
                    "وتمت محاولة إعادة إرسالها."
                )
            except Exception:
                log.exception("Reply media resend failed")
                await notify(
                    owner_chat_id,
                    "تعذرت إعادة إرسال الوسيط الموجود في الرسالة التي رددت عليها."
                )
        else:
            log.info("Owner reply contains no accessible third-party media")
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
            log.exception("Incoming media forwarding failed")


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
                log.exception("Deleted media resend failed")

        forget_snapshot(connection_id, chat_id, message_id)


async def handle_update(update: dict):
    if "business_connection" in update:
        save_business_connection(update["business_connection"])
    elif "business_message" in update:
        await process_business_message(update["business_message"])
    elif "edited_business_message" in update:
        await process_business_message(
            update["edited_business_message"],
            edited=True,
        )
    elif "deleted_business_messages" in update:
        await process_deleted_messages(update["deleted_business_messages"])


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
                "business_connection",
                "business_message",
                "edited_business_message",
                "deleted_business_messages",
            ],
            "drop_pending_updates": False,
        })
        log.info("Telegram webhook registered")
    except Exception:
        log.exception("Webhook registration failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await register_webhook()
    yield


app = FastAPI(
    title="Telegram Business Monitor - RAM PoC",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "service": "Telegram Business Monitor",
        "storage": "RAM only",
        "status": "running",
    }


@app.get("/health")
async def health():
    prune_memory()
    return {
        "ok": True,
        "storage": "RAM only",
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
        log.exception("Update processing failed")
        raise HTTPException(status_code=500, detail="Update processing failed")

    return {"ok": True, "processed": True}
