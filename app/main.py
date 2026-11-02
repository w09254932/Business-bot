import os
import json
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/business_bot.db")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/app.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("business-bot")


def now():
    return datetime.now(timezone.utc).isoformat()


def extract_media(message: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return media type, file_id, file_unique_id."""
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
            return kind, item.get("file_id"), item.get("file_unique_id")

    return None, None, None


async def db():
    conn = await aiosqlite.connect(DATABASE_PATH)
    conn.row_factory = aiosqlite.Row
    return conn


async def init_db():
    async with await db() as conn:
        await conn.executescript("""
        CREATE TABLE IF NOT EXISTS processed_updates (
            update_id INTEGER PRIMARY KEY,
            processed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS connections (
            connection_id TEXT PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            owner_chat_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            can_read_messages INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            connection_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            sender_id INTEGER,
            message_date INTEGER,
            text_content TEXT,
            media_type TEXT,
            file_id TEXT,
            file_unique_id TEXT,
            raw_json TEXT,
            saved_at TEXT NOT NULL,
            PRIMARY KEY (connection_id, chat_id, message_id)
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_message
        ON snapshots(connection_id, message_id);
        """)
        await conn.commit()


async def telegram(method: str, payload: dict):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")

    async with httpx.AsyncClient(timeout=35) as client:
        response = await client.post(f"{API}/{method}", json=payload)
        response.raise_for_status()
        result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API {method}: {result}")

    return result.get("result")


async def notify(owner_chat_id: int, text: str):
    try:
        await telegram("sendMessage", {
            "chat_id": owner_chat_id,
            "text": text[:4000],
        })
    except Exception:
        log.exception("Could not send notification to owner")


async def send_saved_media(owner_chat_id: int, media_type: str, file_id: str):
    methods = {
        "photo": "sendPhoto",
        "video": "sendVideo",
        "voice": "sendVoice",
        "video_note": "sendVideoNote",
        "audio": "sendAudio",
        "document": "sendDocument",
        "animation": "sendAnimation",
        "sticker": "sendSticker",
    }
    method = methods.get(media_type)
    if not method:
        raise ValueError(f"Unsupported media type: {media_type}")

    field = "photo" if media_type == "photo" else media_type
    return await telegram(method, {
        "chat_id": owner_chat_id,
        field: file_id,
    })


async def get_connection(connection_id: str):
    async with await db() as conn:
        cur = await conn.execute(
            "SELECT * FROM connections WHERE connection_id=?",
            (connection_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def save_business_connection(data: dict):
    connection_id = data.get("id")
    user = data.get("user") or {}
    rights = data.get("rights") or {}

    if not connection_id or not user.get("id") or not data.get("user_chat_id"):
        log.warning("Business connection missing required fields")
        return

    enabled = bool(data.get("is_enabled"))
    can_read = bool(rights.get("can_read_messages"))

    async with await db() as conn:
        if not enabled:
            await conn.execute(
                "DELETE FROM connections WHERE connection_id=?",
                (connection_id,),
            )
            await conn.commit()
            log.info("Business connection disabled: %s", connection_id)
            return

        await conn.execute("""
            INSERT INTO connections
            (connection_id, owner_id, owner_chat_id, enabled,
             can_read_messages, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
              owner_id=excluded.owner_id,
              owner_chat_id=excluded.owner_chat_id,
              enabled=excluded.enabled,
              can_read_messages=excluded.can_read_messages,
              updated_at=excluded.updated_at
        """, (
            connection_id,
            user["id"],
            data["user_chat_id"],
            int(enabled),
            int(can_read),
            now(),
        ))
        await conn.commit()

    log.info(
        "Business connection saved: %s | can_read_messages=%s",
        connection_id, can_read
    )


async def store_snapshot(connection_id: str, message: dict):
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return

    sender_id = (message.get("from") or {}).get("id")
    media_type, file_id, unique_id = extract_media(message)
    content = message.get("text") or message.get("caption") or ""

    async with await db() as conn:
        await conn.execute("""
            INSERT INTO snapshots
            (connection_id, chat_id, message_id, sender_id, message_date,
             text_content, media_type, file_id, file_unique_id, raw_json, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(connection_id, chat_id, message_id) DO UPDATE SET
              sender_id=excluded.sender_id,
              message_date=excluded.message_date,
              text_content=excluded.text_content,
              media_type=excluded.media_type,
              file_id=excluded.file_id,
              file_unique_id=excluded.file_unique_id,
              raw_json=excluded.raw_json,
              saved_at=excluded.saved_at
        """, (
            connection_id, chat_id, message_id, sender_id,
            message.get("date"), content, media_type, file_id,
            unique_id, json.dumps(message, ensure_ascii=False), now()
        ))
        await conn.commit()


async def get_snapshot(connection_id: str, chat_id: int, message_id: int):
    async with await db() as conn:
        cur = await conn.execute("""
            SELECT * FROM snapshots
            WHERE connection_id=? AND chat_id=? AND message_id=?
        """, (connection_id, chat_id, message_id))
        row = await cur.fetchone()
        return dict(row) if row else None


async def process_business_message(message: dict, edited: bool = False):
    connection_id = message.get("business_connection_id")
    if not connection_id:
        log.warning("Business message without business_connection_id")
        return

    connection = await get_connection(connection_id)
    if not connection or not connection["enabled"]:
        log.warning("Unknown or disabled business connection: %s", connection_id)
        return

    sender_id = (message.get("from") or {}).get("id")
    owner_id = connection["owner_id"]
    owner_chat_id = connection["owner_chat_id"]
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")

    # Owner replies to a media message: attempt to rescue the replied media.
    if sender_id == owner_id:
        original = message.get("reply_to_message") or {}
        media_type, file_id, _ = extract_media(original)

        if media_type and file_id:
            try:
                await send_saved_media(owner_chat_id, media_type, file_id)
                log.info(
                    "Reply-media resend succeeded: type=%s message_id=%s",
                    media_type, original.get("message_id")
                )
                await notify(
                    owner_chat_id,
                    "تم إرسال نسخة من الوسيط الموجود في الرسالة التي رددت عليها. "
                    "هذا لا يثبت أنه وسيط مؤقت."
                )
            except Exception as exc:
                log.exception("Reply-media resend failed")
                await notify(
                    owner_chat_id,
                    f"تعذرت إعادة إرسال الوسيط من الرد: {str(exc)[:500]}"
                )
        else:
            log.info("Owner message: no accessible media in reply_to_message")
        return

    # Only retain messages from other participants.
    if edited:
        old = await get_snapshot(connection_id, chat_id, message_id)
        new_text = message.get("text") or message.get("caption") or ""
        old_text = old.get("text_content", "") if old else ""

        await store_snapshot(connection_id, message)

        if old_text != new_text:
            await notify(
                owner_chat_id,
                "✏️ تم تعديل رسالة.\n"
                f"معرّف المحادثة: {chat_id}\n"
                f"معرّف الرسالة: {message_id}\n"
                f"النص السابق: {old_text[:1200] or '(لا يوجد نص)'}\n"
                f"النص الحالي: {new_text[:1200] or '(لا يوجد نص)'}"
            )
        else:
            await notify(
                owner_chat_id,
                "✏️ تم تعديل رسالة، لكن لم يتغير النص أو التعليق الظاهر.\n"
                f"معرّف الرسالة: {message_id}"
            )
    else:
        await store_snapshot(connection_id, message)
        log.info(
            "Incoming message stored: connection=%s chat=%s message=%s",
            connection_id, chat_id, message_id
        )


async def process_deleted_messages(data: dict):
    connection_id = data.get("business_connection_id")
    chat_id = (data.get("chat") or {}).get("id")
    message_ids = data.get("message_ids") or []

    connection = await get_connection(connection_id or "")
    if not connection or chat_id is None:
        log.warning("Deletion update has no matching connection/chat")
        return

    owner_chat_id = connection["owner_chat_id"]

    for message_id in message_ids:
        snapshot = await get_snapshot(connection_id, chat_id, message_id)

        if not snapshot:
            await notify(
                owner_chat_id,
                f"🗑️ تم حذف رسالة، لكن لا توجد نسخة محفوظة لها.\n"
                f"معرّف الرسالة: {message_id}"
            )
            continue

        await notify(
            owner_chat_id,
            "🗑️ تم حذف رسالة.\n"
            f"معرّف الرسالة: {message_id}\n"
            f"النص المحفوظ: {snapshot.get('text_content') or '(لا يوجد نص)'}"
        )

        media_type = snapshot.get("media_type")
        file_id = snapshot.get("file_id")
        if media_type and file_id:
            try:
                await send_saved_media(owner_chat_id, media_type, file_id)
            except Exception:
                log.exception("Could not resend deleted message media")

        async with await db() as conn:
            await conn.execute("""
                DELETE FROM snapshots
                WHERE connection_id=? AND chat_id=? AND message_id=?
            """, (connection_id, chat_id, message_id))
            await conn.commit()


async def handle_update(update: dict):
    if "business_connection" in update:
        await save_business_connection(update["business_connection"])
        return

    if "business_message" in update:
        await process_business_message(update["business_message"])
        return

    if "edited_business_message" in update:
        await process_business_message(update["edited_business_message"], edited=True)
        return

    if "deleted_business_messages" in update:
        await process_deleted_messages(update["deleted_business_messages"])
        return

    log.info("Ignored update type: %s", list(update.keys()))


async def register_webhook():
    if not BOT_TOKEN:
        log.warning("BOT_TOKEN is not configured; webhook registration skipped")
        return

    base_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base_url:
        log.info("RENDER_EXTERNAL_URL missing; automatic webhook setup skipped")
        return

    if not WEBHOOK_SECRET:
        log.error("WEBHOOK_SECRET is missing; webhook registration skipped")
        return

    try:
        result = await telegram("setWebhook", {
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
        log.info("Webhook registration result: %s", result)
    except Exception:
        log.exception("Webhook registration failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await register_webhook()
    yield


app = FastAPI(
    title="Telegram Business Monitor PoC",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "service": "Telegram Business Monitor PoC",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"ok": True, "time": now()}


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

    # Telegram may retry webhook deliveries. Process each update once.
    async with await db() as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO processed_updates(update_id, processed_at) VALUES (?, ?)",
            (update_id, now()),
        )
        await conn.commit()
        if cur.rowcount == 0:
            return {"ok": True, "duplicate": True}

    try:
        await handle_update(update)
    except Exception:
        log.exception("Update processing failed: update_id=%s", update_id)
        async with await db() as conn:
            await conn.execute(
                "DELETE FROM processed_updates WHERE update_id=?",
                (update_id,),
            )
            await conn.commit()
        raise HTTPException(status_code=500, detail="Update processing failed")

    return {"ok": True, "processed": True}
