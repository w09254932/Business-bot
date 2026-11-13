from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from battle_core import compute_battle
from widget_calculator import (
    encode_widget_answer,
    get_history_page,
    load_widget_html,
    parse_widget_request,
    save_battle_once,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("tr_battle")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
TELEGRAM_WEBHOOK_SECRET = hashlib.sha256(WEBHOOK_SECRET.encode("utf-8")).hexdigest() if WEBHOOK_SECRET else ""
WEBHOOK_PATH = "/telegram/webhook"
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()


def init_firestore():
    """Initialize Firebase Admin from a JSON environment variable, if present."""
    if not FIREBASE_CREDENTIALS_JSON:
        log.warning("FIREBASE_CREDENTIALS_JSON is not set; battle history is disabled")
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            info = json.loads(FIREBASE_CREDENTIALS_JSON)
            firebase_admin.initialize_app(credentials.Certificate(info))
        return firestore.client()
    except Exception:
        log.exception("Firestore initialization failed; history is disabled")
        return None


async def telegram_api(client: httpx.AsyncClient, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "description": "BOT_TOKEN غير مضبوط في إعدادات الخدمة."}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        response = await client.post(url, json=payload)
        try:
            body = response.json()
        except ValueError:
            return {"ok": False, "description": f"Telegram returned HTTP {response.status_code}"}
        if not isinstance(body, dict):
            return {"ok": False, "description": "Invalid Telegram API response"}
        if response.status_code >= 400:
            body.setdefault("description", f"Telegram HTTP {response.status_code}")
        return body
    except httpx.HTTPError:
        log.exception("Telegram API request failed: %s", method)
        return {"ok": False, "description": "تعذّر الاتصال بخدمة Telegram."}


async def send_widget(chat_id: int, client: httpx.AsyncClient) -> bool:
    try:
        html = load_widget_html()
    except Exception:
        log.exception("Widget HTML validation failed")
        await telegram_api(client, "sendMessage", {
            "chat_id": chat_id,
            "text": "⚠️ تعذّر تحميل واجهة الحاسبة. راجع سجل الخدمة.",
        })
        return False

    response = await telegram_api(client, "sendWidget", {
        "chat_id": chat_id,
        "html": html,
        "allow_queries": True,
    })
    if response.get("ok"):
        return True

    log.error("sendWidget failed: %s", response.get("description", "unknown error"))
    await telegram_api(client, "sendMessage", {
        "chat_id": chat_id,
        "text": "⚠️ لم يتمكن تيليجرام من إرسال الحاسبة. تأكد من تحديث تطبيق Telegram وأن البوت يعمل.",
    })
    return False


async def handle_message(message: dict[str, Any], client: httpx.AsyncClient) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return

    text = (message.get("text") or "").strip()
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    if command in {"/start", "/widget", "/battle", "/history"} or text in {"حاسبة", "حاسبه", "معركة الشعبية"}:
        if chat.get("type") != "private":
            await telegram_api(client, "sendMessage", {
                "chat_id": chat_id,
                "text": "افتح البوت بالخاص لاستخدام حاسبة TR التفاعلية.",
            })
            return
        await send_widget(chat_id, client)
        return

    if command in {"/help", "/start_help"}:
        await telegram_api(client, "sendMessage", {
            "chat_id": chat_id,
            "text": (
                "⚔️ TR Battle Calculator\n\n"
                "/start — فتح الحاسبة\n"
                "/widget — فتح الحاسبة\n"
                "/history — عرض سجل معاركك داخل الحاسبة\n\n"
                "اختر نوع المعركة وأدخل شعبيتك وشعبية الخصم من لوحة الأرقام."
            ),
        })


async def handle_widget_query(query: dict[str, Any], client: httpx.AsyncClient, db: Any) -> None:
    query_id = str(query.get("id") or "")
    user = query.get("from") or {}
    if not query_id or not isinstance(user.get("id"), int):
        log.warning("Widget query missing id or authenticated Telegram user")
        return

    try:
        parsed = parse_widget_request(query.get("data", ""))
        if parsed[0] == "history":
            _, mode, page = parsed
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(get_history_page, db, int(user["id"]), mode, page),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                response = {"kind": "history", "ok": False, "message": "تأخر تحميل السجل. حاول مرة أخرى."}
        else:
            _, mode, mine, opponent = parsed
            result = compute_battle(mine, opponent, mode)
            saved = False
            if db is not None:
                try:
                    saved = await asyncio.wait_for(
                        asyncio.to_thread(save_battle_once, db, query_id, user, mode, mine, opponent, result),
                        timeout=4.0,
                    )
                except asyncio.TimeoutError:
                    # The worker thread may still finish the idempotent write.
                    saved = False
                except Exception:
                    log.exception("Battle history write failed")
            response = {
                "kind": "result", "ok": True, "mode": mode,
                "mine": mine, "opponent": opponent,
                "my_points": result["my_points"], "opp_points": result["opp_points"],
                "result": result["result_label"],
                "my_result": str(int(result["my_result"])) if float(result["my_result"]).is_integer() else f"{result['my_result']:.1f}",
                "opp_result": str(int(result["opp_result"])) if float(result["opp_result"]).is_integer() else f"{result['opp_result']:.1f}",
                "saved": saved,
            }
    except ValueError as exc:
        response = {"kind": "error", "ok": False, "message": str(exc)}
    except Exception:
        log.exception("Widget request processing failed")
        response = {"kind": "error", "ok": False, "message": "حدث خطأ أثناء تنفيذ الطلب."}

    answer = await telegram_api(client, "answerWidgetQuery", {
        "widget_query_id": query_id,
        "data": encode_widget_answer(response),
    })
    if not answer.get("ok"):
        log.warning("answerWidgetQuery failed: %s", answer.get("description", "unknown error"))



async def register_webhook(client: httpx.AsyncClient) -> None:
    if not BOT_TOKEN:
        log.warning("BOT_TOKEN is not set; webhook registration skipped")
        return
    base_url = os.getenv("WEBHOOK_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if not base_url:
        log.warning("WEBHOOK_URL / RENDER_EXTERNAL_URL is not set; register webhook manually")
        return
    if not WEBHOOK_SECRET:
        raise RuntimeError("WEBHOOK_SECRET is required when registering a public webhook")
    webhook_url = base_url.rstrip("/")
    if not webhook_url.endswith(WEBHOOK_PATH):
        webhook_url += WEBHOOK_PATH

    payload: dict[str, Any] = {
        "url": webhook_url,
        "allowed_updates": ["message", "widget_query"],
    }
    if TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = TELEGRAM_WEBHOOK_SECRET
    result = await telegram_api(client, "setWebhook", payload)
    if result.get("ok"):
        log.info("Telegram webhook registered: %s", webhook_url)
    else:
        log.error("Telegram webhook registration failed: %s", result.get("description", "unknown error"))


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.http = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0))
    application.state.db = init_firestore()
    await register_webhook(application.state.http)
    try:
        yield
    finally:
        await application.state.http.aclose()


app = FastAPI(title="TR Battle Calculator", version="1.0.0", lifespan=lifespan)


@app.get("/")
async def root():
    return {"service": "TR Battle Calculator", "status": "ok"}


@app.get("/health")
async def health(request: Request):
    return {
        "status": "ok",
        "telegram_configured": bool(BOT_TOKEN),
        "firestore_enabled": request.app.state.db is not None if hasattr(request.app.state, "db") else False,
    }


@app.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if TELEGRAM_WEBHOOK_SECRET and not hmac.compare_digest(x_telegram_bot_api_secret_token or "", TELEGRAM_WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    try:
        update = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Update must be an object")

    client: httpx.AsyncClient = request.app.state.http
    if isinstance(update.get("widget_query"), dict):
        await handle_widget_query(update["widget_query"], client, request.app.state.db)
    elif isinstance(update.get("message"), dict):
        await handle_message(update["message"], client)
    return JSONResponse({"ok": True})
