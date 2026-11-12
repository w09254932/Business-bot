# -*- coding: utf-8 -*-
"""Telegram Bot API 10.4 widget bot for the popularity-battle calculator.

A dedicated bot token is used so the existing aiogram webhook can remain unchanged.
Environment:
  BATTLE_WIDGET_BOT_TOKEN (required)
  BATTLE_WIDGET_WEBHOOK_SECRET (recommended; A-Z/a-z/0-9/_/- only)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

_API = "https://api.telegram.org/bot{token}/{method}"
_WIDGET_TOKEN = ""
_WIDGET_SECRET = ""
_WEBHOOK_HOST = ""
_DB = None
_LOGGER = logging.getLogger("battle-widget")
_COMPUTE_BATTLE = None
_SESSION: aiohttp.ClientSession | None = None


def _session() -> aiohttp.ClientSession:
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        _SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
    return _SESSION


async def _api(token: str, method: str, payload: dict) -> dict:
    try:
        async with _session().post(_API.format(token=token, method=method), json=payload) as resp:
            data = await resp.json(content_type=None)
            if not isinstance(data, dict):
                return {"ok": False, "description": "Invalid Telegram API response"}
            return data
    except Exception:
        _LOGGER.exception("Telegram API call failed: %s", method)
        return {"ok": False, "description": "Telegram API request failed"}


def _widget_html() -> str:
    path = Path(__file__).with_name("battle_calculator_telegram_widget.html")
    html = path.read_text(encoding="utf-8")
    return html


def _safe_int(value: str) -> int | None:
    if not value or not value.isascii() or not value.isdigit() or len(value) > 15:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _persist_battle(query_id: str, user: dict, mode: str, mine: int, opp: int, result: dict) -> None:
    """Idempotently save a result using Telegram's query id as the document id."""
    if _DB is None:
        return
    from firebase_admin import firestore as fb_firestore
    uid = user.get("id")
    if not isinstance(uid, int):
        return
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
    ref = _DB.collection("battles").document("widget_" + query_id)
    data = {
        "user_id": uid,
        "username": user.get("username"),
        "name": name,
        "mode": mode,
        "source": "widget",
        "my_number": mine,
        "my_points": result["my_points"],
        "opp_number": opp,
        "opp_points": result["opp_points"],
        "result": result["result_label"],
        "my_result": result["my_result"],
        "opp_result": result["opp_result"],
        "ts": datetime.now(timezone.utc),
    }
    try:
        ref.create(data)
    except Exception as exc:
        # A duplicate webhook delivery must not increment counters twice.
        if "already exists" in str(exc).lower() or "alreadyexists" in str(exc).lower():
            return
        raise
    _DB.collection("stats").document("counters").set(
        {"total": fb_firestore.Increment(1), f"mode_{mode}": fb_firestore.Increment(1)},
        merge=True,
    )
    # Keep the same latest-three-per-mode policy as the original calculator.
    docs = list(_DB.collection("battles").where("user_id", "==", uid).limit(100).stream())
    same = [d for d in docs if (d.to_dict() or {}).get("mode") == mode]
    same.sort(key=lambda d: (d.to_dict() or {}).get("ts") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for old in same[3:]:
        old.reference.delete()


async def _answer_query(token: str, query: dict, data: str | None = None) -> None:
    payload = {"widget_query_id": str(query.get("id", ""))}
    if data is not None:
        payload["data"] = data
    await _api(token, "answerWidgetQuery", payload)


async def _handle_widget_query(token: str, query: dict) -> None:
    qid = str(query.get("id", ""))
    user = query.get("from") or {}
    parts = (query.get("data") or "").split("|")
    if len(parts) != 4 or parts[0] != "c":
        await _answer_query(token, query, json.dumps({"error": "INVALID_REQUEST"}))
        return
    _, mode, raw_mine, raw_opp = parts
    if mode not in {"individual", "home", "team"}:
        await _answer_query(token, query, json.dumps({"error": "INVALID_MODE"}))
        return
    mine, opp = _safe_int(raw_mine), _safe_int(raw_opp)
    if mine is None or opp is None or _COMPUTE_BATTLE is None:
        await _answer_query(token, query, json.dumps({"error": "INVALID_NUMBERS"}))
        return
    try:
        computed = _COMPUTE_BATTLE(mine, opp, mode)
        result = {
            "mode": mode,
            "my": mine,
            "opp": opp,
            "my_points": computed["my_points"],
            "opp_points": computed["opp_points"],
            "result": "فوز" if mine > opp else "خسارة",
            "my_result": computed["my_result"],
            "opp_result": computed["opp_result"],
            "result_label": computed["result_label"],
        }
        answer = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if len(answer.encode("utf-8")) > 1024:
            answer = json.dumps({"error": "RESULT_TOO_LARGE"})
    except Exception:
        _LOGGER.exception("Battle widget calculation failed")
        answer = json.dumps({"error": "CALCULATION_FAILED"})
        result = None
    # Return the answer promptly; persistence happens after the widget has its result.
    await _answer_query(token, query, answer)
    if result is not None and qid and user.get("id"):
        try:
            await asyncio.to_thread(_persist_battle, qid, user, mode, mine, opp, computed)
        except Exception:
            _LOGGER.exception("Could not persist widget battle %s", qid)


async def _handle_update(token: str, update: dict) -> None:
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    if text.startswith("/start") and chat.get("type") == "private":
        response = await _api(token, "sendWidget", {
            "chat_id": chat["id"],
            "html": _widget_html(),
            "caption": "⚔️ حاسبة معركة الشعبية | الفردية والمنزل والفريق والحاسبة العكسية",
            "allow_queries": True,
        })
        if not response.get("ok"):
            _LOGGER.error("sendWidget failed: %s", response)
        return
    query = update.get("widget_query")
    if query:
        await _handle_widget_query(token, query)


async def _webhook(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "")
    if not _WIDGET_TOKEN or token != _WIDGET_TOKEN:
        return web.Response(status=404, text="not found")
    if _WIDGET_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != _WIDGET_SECRET:
        return web.Response(status=403, text="forbidden")
    try:
        update = await request.json()
    except Exception:
        return web.Response(status=400, text="bad request")
    # Telegram expects a fast 200 response. Work is handled asynchronously.
    asyncio.create_task(_handle_update(token, update))
    return web.Response(text="ok")


async def _startup(app: web.Application) -> None:
    if not _WIDGET_TOKEN:
        _LOGGER.warning("BATTLE_WIDGET_BOT_TOKEN is not configured; widget bot disabled")
        return
    me = await _api(_WIDGET_TOKEN, "getMe", {})
    if not me.get("ok"):
        _LOGGER.error("Widget bot token validation failed: %s", me)
        return
    payload = {
        "url": f"{_WEBHOOK_HOST}/battle-widget/{_WIDGET_TOKEN}",
        "drop_pending_updates": True,
        "allowed_updates": ["message", "widget_query"],
    }
    if _WIDGET_SECRET:
        payload["secret_token"] = _WIDGET_SECRET
    result = await _api(_WIDGET_TOKEN, "setWebhook", payload)
    if result.get("ok"):
        _LOGGER.info("Battle widget bot enabled: @%s", (me.get("result") or {}).get("username"))
    else:
        _LOGGER.error("Could not set widget bot webhook: %s", result)


async def _cleanup(app: web.Application) -> None:
    global _SESSION
    if _SESSION and not _SESSION.closed:
        await _SESSION.close()
    _SESSION = None


def setup_widgetbot(app: web.Application, db, webhook_host: str, logger: logging.Logger, compute_battle) -> None:
    """Register the raw Bot API 10.4 webhook for a dedicated calculator bot."""
    global _WIDGET_TOKEN, _WIDGET_SECRET, _WEBHOOK_HOST, _DB, _LOGGER, _COMPUTE_BATTLE
    _WIDGET_TOKEN = (os.environ.get("BATTLE_WIDGET_BOT_TOKEN") or "").strip()
    _WIDGET_SECRET = (os.environ.get("BATTLE_WIDGET_WEBHOOK_SECRET") or "").strip()
    _WEBHOOK_HOST = (webhook_host or "").rstrip("/")
    _DB = db
    _LOGGER = logger or _LOGGER
    _COMPUTE_BATTLE = compute_battle
    app.router.add_post("/battle-widget/{token}", _webhook)
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
