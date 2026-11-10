from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from telethon import TelegramClient, utils

from app.store import Store

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
ARCHIVE_CHANNEL_ID = int(os.getenv("ARCHIVE_CHANNEL_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "data/index.sqlite3")
SESSION_PATH = os.getenv("SESSION_PATH", "data/archive_bot")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not API_ID or not API_HASH:
    raise RuntimeError("API_ID and API_HASH are required for MTProto channel reads")
if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET is required")
if not ARCHIVE_CHANNEL_ID:
    raise RuntimeError("ARCHIVE_CHANNEL_ID is required")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("business-archive")

store = Store(DB_PATH)
telegram_client: TelegramClient | None = None
archive_entity: Any = None
update_lock = asyncio.Lock()

BOT_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAX_CHANNEL_TEXT = 3500


async def bot_api(method: str, payload: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.post(f"{BOT_API}/{method}", json=payload)
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Telegram returned invalid JSON for {method}") from exc
    if response.status_code >= 400 or not data.get("ok"):
        raise RuntimeError(
            f"Telegram {method} failed ({data.get('error_code', response.status_code)}): "
            f"{data.get('description', 'unknown error')}"
        )
    return data.get("result")


def split_text(value: str, limit: int = MAX_CHANNEL_TEXT) -> list[str]:
    value = value or "[رسالة بدون نص أو تعليق]"
    if len(value) <= limit:
        return [value]
    chunks: list[str] = []
    remaining = value
    while remaining:
        cut = min(limit, len(remaining))
        # Prefer a newline/space boundary without losing any characters.
        if cut < len(remaining):
            boundary = max(remaining.rfind("\n", 0, cut), remaining.rfind(" ", 0, cut))
            if boundary >= int(limit * 0.65):
                cut = boundary + 1
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def message_text(message: dict[str, Any]) -> str:
    # The archive MVP stores text and captions. Media binaries are not copied yet.
    return str(message.get("text") or message.get("caption") or "")


def sender_label(message: dict[str, Any]) -> str:
    sender = message.get("from") or {}
    name = " ".join(
        str(sender.get(k, "")).strip()
        for k in ("first_name", "last_name")
        if sender.get(k)
    ).strip()
    username = f"@{sender['username']}" if sender.get("username") else "بدون يوزر"
    sender_id = sender.get("id", "غير متوفر")
    return f"{name or 'اسم غير متوفر'} | {username} | ID:{sender_id}"


def direction_for(message: dict[str, Any], owner_id: int) -> str:
    sender_id = (message.get("from") or {}).get("id")
    return "outgoing" if sender_id == owner_id else "incoming"


def channel_header(
    *, event: str, owner_id: int, connection_id: str, chat_id: int,
    source_message_id: int, direction: str, sender: str, extra: str = "",
) -> str:
    # All original text lives in separate channel messages, not the SQL database.
    labels = {
        "NEW": "🆕 رسالة جديدة",
        "EDIT": "✏️ تعديل رسالة",
        "DELETE": "🗑️ حذف رسالة",
    }
    rows = [
        labels[event],
        f"صاحب الحساب: {owner_id}",
        f"Business connection: {connection_id}",
        f"الاتجاه: {'صادرة' if direction == 'outgoing' else 'واردة'}",
        f"المرسل: {sender}",
        f"Chat ID: {chat_id}",
        f"Message ID: {source_message_id}",
    ]
    if extra:
        rows.append(extra)
    return "\n".join(rows)


async def send_channel_text(text: str, reply_to: int | None = None) -> int:
    payload: dict[str, Any] = {
        "chat_id": ARCHIVE_CHANNEL_ID,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    }
    if reply_to is not None:
        payload["reply_parameters"] = {"message_id": reply_to}
    result = await bot_api("sendMessage", payload)
    return int(result["message_id"])


async def publish_snapshot(text: str, reply_to: int) -> list[int]:
    ids: list[int] = []
    for chunk in split_text(text):
        ids.append(await send_channel_text(chunk, reply_to=reply_to))
    return ids


async def publish_labeled_snapshot(label: str, text: str, reply_to: int) -> list[int]:
    await send_channel_text(label, reply_to=reply_to)
    return await publish_snapshot(text, reply_to=reply_to)


async def owner_notify(owner_chat_id: int, title: str, details: list[tuple[str, str]]) -> None:
    # Send separate messages so long old/new texts are not silently truncated.
    await bot_api("sendMessage", {"chat_id": owner_chat_id, "text": title})
    for label, content in details:
        await bot_api("sendMessage", {"chat_id": owner_chat_id, "text": label})
        for chunk in split_text(content):
            await bot_api("sendMessage", {"chat_id": owner_chat_id, "text": chunk})


async def get_archive_entity() -> Any:
    global archive_entity
    if telegram_client is None:
        raise RuntimeError("MTProto client is not running")
    if archive_entity is not None:
        return archive_entity
    try:
        archive_entity = await telegram_client.get_entity(ARCHIVE_CHANNEL_ID)
        return archive_entity
    except Exception:
        # Resolve by scanning dialogs if the channel ID is not yet in the session cache.
        async for dialog in telegram_client.iter_dialogs():
            if utils.get_peer_id(dialog.entity) == ARCHIVE_CHANNEL_ID:
                archive_entity = dialog.entity
                return archive_entity
        raise RuntimeError(
            "Archive channel is not visible to the bot's MTProto session. "
            "Add the bot to the private channel and grant posting access."
        )


async def read_snapshot(snapshot_ids: list[int]) -> str:
    """Read the latest text snapshot from the archive channel using MTProto."""
    if not snapshot_ids:
        return "[لا توجد نسخة محفوظة في قناة الأرشيف]"
    entity = await get_archive_entity()
    pieces: list[str] = []
    for message_id in snapshot_ids:
        msg = await telegram_client.get_messages(entity, ids=int(message_id))
        if isinstance(msg, (list, tuple)):
            msg = msg[0] if msg else None
        text = getattr(msg, "message", None) if msg else None
        if text is None:
            raise RuntimeError(f"Archive message {message_id} could not be read")
        pieces.append(str(text))
    return "".join(pieces)


async def handle_connection(data: dict[str, Any]) -> None:
    connection_id = str(data.get("id") or "")
    user = data.get("user") or {}
    owner_id = user.get("id")
    owner_chat_id = data.get("user_chat_id")
    if not connection_id or owner_id is None or owner_chat_id is None:
        log.warning("Ignoring incomplete business connection update")
        return
    store.save_connection(
        connection_id=connection_id,
        owner_id=int(owner_id),
        owner_chat_id=int(owner_chat_id),
        enabled=bool(data.get("is_enabled")),
    )
    log.info("Business connection updated: owner_id=%s enabled=%s", owner_id, data.get("is_enabled"))


async def handle_new_message(message: dict[str, Any]) -> None:
    connection_id = str(message.get("business_connection_id") or "")
    chat_id = (message.get("chat") or {}).get("id")
    source_message_id = message.get("message_id")
    if not connection_id or chat_id is None or source_message_id is None:
        return
    connection = store.get_connection(connection_id)
    if not connection or not connection["enabled"]:
        log.warning("New business message has no enabled connection: %s", connection_id)
        return

    owner_id = int(connection["owner_id"])
    owner_chat_id = int(connection["owner_chat_id"])
    direction = direction_for(message, owner_id)
    text = message_text(message)
    header = await send_channel_text(channel_header(
        event="NEW", owner_id=owner_id, connection_id=connection_id,
        chat_id=int(chat_id), source_message_id=int(source_message_id),
        direction=direction, sender=sender_label(message),
    ))
    snapshot_ids = await publish_snapshot(text, reply_to=header)
    store.save_message(
        connection_id=connection_id,
        chat_id=int(chat_id),
        source_message_id=int(source_message_id),
        owner_id=owner_id,
        owner_chat_id=owner_chat_id,
        direction=direction,
        sender=sender_label(message),
        original_header_id=header,
        latest_snapshot_ids=snapshot_ids,
        deleted=False,
    )
    # Intentionally no notification for a new message.


async def handle_edited_message(message: dict[str, Any]) -> None:
    connection_id = str(message.get("business_connection_id") or "")
    chat_id = (message.get("chat") or {}).get("id")
    source_message_id = message.get("message_id")
    if not connection_id or chat_id is None or source_message_id is None:
        return
    connection = store.get_connection(connection_id)
    if not connection:
        return
    record = store.get_message(connection_id, int(chat_id), int(source_message_id))
    if not record:
        # The original may predate the bot's connection or the local index.
        await handle_new_message(message)
        owner_id = int(connection["owner_id"])
        await owner_notify(
            int(connection["owner_chat_id"]),
            "⚠️ تم رصد تعديل، لكن لم نعثر على النسخة السابقة في قناة الأرشيف.",
            [("النص الحالي:", message_text(message))],
        )
        return

    if record["deleted"]:
        log.info("Ignoring edit for an already deleted message")
        return

    old_text = await read_snapshot(record["latest_snapshot_ids"])
    new_text = message_text(message)
    owner_id = int(record["owner_id"])
    direction = record["direction"]
    header = await send_channel_text(channel_header(
        event="EDIT", owner_id=owner_id, connection_id=connection_id,
        chat_id=int(chat_id), source_message_id=int(source_message_id),
        direction=direction, sender=record["sender"],
        extra=f"النسخة السابقة: {record['latest_snapshot_ids']} | قبل التعديل: من سجل القناة",
    ), reply_to=int(record["original_header_id"]))
    await publish_labeled_snapshot("⬅️ النص السابق", old_text, reply_to=header)
    new_snapshot_ids = await publish_labeled_snapshot("➡️ النص الجديد", new_text, reply_to=header)
    store.update_latest_snapshot(
        connection_id, int(chat_id), int(source_message_id), new_snapshot_ids
    )
    await owner_notify(
        int(record["owner_chat_id"]),
        f"✏️ تم تعديل رسالة {'أرسلتها' if direction == 'outgoing' else 'وصلتك'}\n"
        f"معرف المحادثة: {chat_id}\nمعرف الرسالة: {source_message_id}",
        [("النص السابق:", old_text), ("النص الجديد:", new_text)],
    )


async def handle_deleted_messages(data: dict[str, Any]) -> None:
    connection_id = str(data.get("business_connection_id") or "")
    chat_id = (data.get("chat") or {}).get("id")
    if not connection_id or chat_id is None:
        return
    connection = store.get_connection(connection_id)
    if not connection:
        return

    for source_message_id in data.get("message_ids") or []:
        record = store.get_message(connection_id, int(chat_id), int(source_message_id))
        if record and record["deleted"]:
            continue
        if record:
            last_text = await read_snapshot(record["latest_snapshot_ids"])
            owner_id = int(record["owner_id"])
            direction = record["direction"]
            sender = record["sender"]
            original_header_id = int(record["original_header_id"])
        else:
            last_text = "[تعذر العثور على نسخة الرسالة في قناة الأرشيف]"
            owner_id = int(connection["owner_id"])
            direction = "incoming"
            sender = "غير معروف"
            original_header_id = None

        header = await send_channel_text(channel_header(
            event="DELETE", owner_id=owner_id, connection_id=connection_id,
            chat_id=int(chat_id), source_message_id=int(source_message_id),
            direction=direction, sender=sender,
            extra="تم الحذف من المحادثة الأصلية؛ المحتوى أدناه مسترجع من قناة الأرشيف.",
        ), reply_to=original_header_id)
        await publish_labeled_snapshot("🗑️ آخر محتوى محفوظ قبل الحذف", last_text, reply_to=header)

        if record:
            store.mark_deleted(connection_id, int(chat_id), int(source_message_id))

        await owner_notify(
            int(connection["owner_chat_id"]),
            f"🗑️ تم حذف رسالة {'أرسلتها' if direction == 'outgoing' else 'وصلتك'}\n"
            f"معرف المحادثة: {chat_id}\nمعرف الرسالة: {source_message_id}",
            [("آخر محتوى محفوظ قبل الحذف:", last_text)],
        )


async def process_update(update: dict[str, Any]) -> None:
    update_id = update.get("update_id")
    if update_id is None:
        return
    async with update_lock:
        if store.update_seen(int(update_id)):
            return
        if "business_connection" in update:
            await handle_connection(update["business_connection"])
        elif "business_message" in update:
            await handle_new_message(update["business_message"])
        elif "edited_business_message" in update:
            await handle_edited_message(update["edited_business_message"])
        elif "deleted_business_messages" in update:
            await handle_deleted_messages(update["deleted_business_messages"])
        elif "message" in update:
            msg = update["message"]
            if msg.get("text", "").startswith("/start"):
                await bot_api("sendMessage", {
                    "chat_id": (msg.get("chat") or {}).get("id"),
                    "text": "أهلاً بك. اربط هذا البوت بحساب Telegram Business من إعدادات الأعمال.",
                })
        store.mark_update_seen(int(update_id))


@asynccontextmanager
async def lifespan(_: FastAPI):
    global telegram_client
    store.initialize()
    Path(SESSION_PATH).parent.mkdir(parents=True, exist_ok=True)
    telegram_client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await telegram_client.start(bot_token=BOT_TOKEN)
    await get_archive_entity()  # Fail early if the bot cannot access the archive channel.
    if WEBHOOK_URL:
        await bot_api("setWebhook", {
            "url": WEBHOOK_URL,
            "secret_token": WEBHOOK_SECRET,
            "allowed_updates": [
                "message", "business_connection", "business_message",
                "edited_business_message", "deleted_business_messages",
            ],
            "drop_pending_updates": False,
        })
        log.info("Webhook configured")
    yield
    if telegram_client:
        await telegram_client.disconnect()


app = FastAPI(title="Business Channel Archive Bot", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "archive": "connected" if archive_entity else "not_ready"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        WEBHOOK_SECRET, x_telegram_bot_api_secret_token
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    update = await request.json()
    try:
        await process_update(update)
    except Exception:
        log.exception("Update processing failed; Telegram may retry it")
        raise HTTPException(status_code=500, detail="Update processing failed")
    return {"ok": True}
