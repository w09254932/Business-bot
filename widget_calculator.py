# -*- coding: utf-8 -*-
"""Telegram HTML Widget sender and widget_query handler for TR Battle Calculator.

The widget HTML is local and self-contained. No external network calls are made
from the widget sandbox; all calculations and history reads happen server-side.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import zlib

import aiohttp

_API = "https://api.telegram.org/bot{token}/{method}"
_WIDGET_HTML = Path(__file__).with_name("widget.html")
_VALID_MODES = {"individual", "home", "team"}
_MODE_LABELS = {"individual": "الفردية", "home": "المنزل", "team": "الفريق"}
_PAGE_SIZE = 5


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _format_number(value: Any) -> str:
    try:
        number = float(value or 0)
        return str(int(number)) if number.is_integer() else f"{number:.1f}"
    except (TypeError, ValueError):
        return "0"


async def _telegram_api(token: str, method: str, payload: dict) -> dict:
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(_API.format(token=token, method=method), json=payload) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                body = await response.text()
                return {"ok": False, "description": f"HTTP {response.status}: {body[:300]}"}
            if response.status >= 400 and isinstance(data, dict):
                data.setdefault("description", f"HTTP {response.status}")
            return data if isinstance(data, dict) else {"ok": False, "description": "Invalid Telegram API response"}


def load_widget_html() -> str:
    html = _WIDGET_HTML.read_text(encoding="utf-8")
    raw = html.encode("utf-8")
    compressed = zlib.compress(raw, level=9)
    if len(raw) > 32768:
        raise ValueError(f"Widget HTML too large: {len(raw)} bytes (max 32768)")
    if len(compressed) > 4096:
        raise ValueError(f"Compressed widget HTML too large: {len(compressed)} bytes (max 4096)")
    return html


async def send_widget(token: str, chat_id: int) -> tuple[bool, str]:
    """Send the actual HTML widget to one private chat."""
    try:
        html = load_widget_html()
        response = await _telegram_api(token, "sendWidget", {
            "chat_id": chat_id,
            "html": html,
            "height": 570,
            "allow_queries": True,
            "caption": "⚔️ حاسبة معركة الشعبية TR",
        })
        if response.get("ok"):
            return True, "تم إرسال الحاسبة. افتحها وابدأ التجربة."
        return False, response.get("description", "تعذّر إرسال Widget")
    except Exception as exc:
        logging.getLogger("battle_widget").exception("send_widget failed")
        return False, str(exc)


def _parse_request(data: str) -> tuple:
    if not isinstance(data, str) or len(data.encode("utf-8")) > 64:
        raise ValueError("بيانات الطلب غير صالحة")
    parts = data.split("|")
    if len(parts) == 4 and parts[0] == "calc":
        _, mode, mine, opponent = parts
        if mode not in _VALID_MODES:
            raise ValueError("نوع المعركة غير صالح")
        if not mine.isascii() or not mine.isdigit() or not opponent.isascii() or not opponent.isdigit():
            raise ValueError("أدخل أرقامًا صحيحة فقط")
        if len(mine) > 15 or len(opponent) > 15:
            raise ValueError("الحد الأقصى 15 خانة لكل رقم")
        return "calc", mode, int(mine), int(opponent)
    if len(parts) == 3 and parts[0] == "history":
        _, mode, page_text = parts
        if mode not in _VALID_MODES | {"all"}:
            raise ValueError("نوع السجل غير صالح")
        if not page_text.isascii() or not page_text.isdigit():
            raise ValueError("رقم الصفحة غير صالح")
        page = int(page_text)
        if page > 1000:
            raise ValueError("رقم الصفحة خارج النطاق")
        return "history", mode, page
    raise ValueError("طلب غير معروف")


def _save_battle_once(db, query_id: str, user: dict, mode: str, mine: int,
                      mine_points: int, opponent: int, opponent_points: int,
                      result_label: str, my_result: float, opp_result: float) -> bool:
    """Save permanently; deterministic doc ID makes webhook retries idempotent.

    Unlike the legacy save_battle(), this does NOT trim old records.
    """
    if db is None:
        return False
    digest = hashlib.sha256(query_id.encode("utf-8")).hexdigest()
    ref = db.collection("battles").document(f"widget_{digest}")
    now = datetime.now(timezone.utc)
    full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])).strip()
    record = {
        "user_id": int(user["id"]),
        "username": user.get("username"),
        "name": full_name or user.get("first_name") or "",
        "mode": mode,
        "source": "widget",
        "my_number": mine,
        "my_points": mine_points,
        "opp_number": opponent,
        "opp_points": opponent_points,
        "result": result_label,
        "my_result": my_result,
        "opp_result": opp_result,
        "ts": now,
        "widget_query_id": query_id,
    }
    try:
        ref.create(record)
    except Exception as exc:
        # A duplicated query is already safely persisted. Avoid masking other DB errors.
        if exc.__class__.__name__ in {"AlreadyExists", "Conflict"}:
            return True
        logging.getLogger("battle_widget").exception("Firestore widget history save failed")
        return False

    try:
        from firebase_admin import firestore
        db.collection("stats").document("counters").set({
            "total": firestore.Increment(1),
            f"mode_{mode}": firestore.Increment(1),
        }, merge=True)
    except Exception:
        logging.getLogger("battle_widget").exception("Widget battle counter update failed")
    return True


def _history_page(db, user_id: int, mode: str, page: int) -> dict:
    if db is None:
        return {"kind": "history", "ok": False, "message": "التخزين غير مفعّل. تحقق من FIREBASE_CREDENTIALS."}
    try:
        query = db.collection("battles").where("user_id", "==", int(user_id))
        if mode != "all":
            query = query.where("mode", "==", mode)
        query = query.order_by("ts", direction="DESCENDING")
        selected_docs = list(query.offset(page * _PAGE_SIZE).limit(_PAGE_SIZE + 1).stream())
        has_more = len(selected_docs) > _PAGE_SIZE
        selected_docs = selected_docs[:_PAGE_SIZE]
        rows = [doc.to_dict() or {} for doc in selected_docs]
        output = []
        for row in rows:
            stamp = row.get("ts")
            if hasattr(stamp, "astimezone"):
                date_text = stamp.astimezone(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
            else:
                date_text = "—"
            output.append({
                "mode": _MODE_LABELS.get(row.get("mode"), "معركة"),
                "result": str(row.get("result", "—")),
                "mine": int(row.get("my_number", 0) or 0),
                "opponent": int(row.get("opp_number", 0) or 0),
                "points": _format_number(row.get("my_result", 0)),
                "date": date_text,
            })
        return {
            "kind": "history", "ok": True, "rows": output,
            "page": page, "has_more": has_more,
            "mode": mode,
        }
    except Exception:
        logging.getLogger("battle_widget").exception("Widget history read failed")
        return {"kind": "history", "ok": False, "message": "تعذّر جلب السجل الآن."}


async def handle_widget_query(token: str, query: dict, db, compute_battle, register_user=None) -> None:
    """Process one Telegram widget_query update and always try to answer it."""
    query_id = str(query.get("id") or "")
    user = query.get("from") or {}
    response: dict
    try:
        if not query_id or not user.get("id"):
            raise ValueError("تعذّر تحديد المستخدم")
        parsed = _parse_request(query.get("data", ""))
        if parsed[0] == "calc":
            _, mode, mine, opponent = parsed
            result = compute_battle(mine, opponent, mode)
            if register_user is not None:
                from types import SimpleNamespace
                display_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])).strip()
                register_user(SimpleNamespace(
                    id=int(user["id"]), username=user.get("username"),
                    full_name=display_name or user.get("first_name") or "",
                ))
            saved = _save_battle_once(
                db, query_id, user, mode, mine, result["my_points"], opponent,
                result["opp_points"], result["result_label"], result["my_result"],
                result["opp_result"],
            )
            response = {
                "kind": "result", "ok": True, "mode": mode,
                "mine": mine, "opponent": opponent,
                "my_points": result["my_points"], "opp_points": result["opp_points"],
                "result": result["result_label"],
                "my_result": _format_number(result["my_result"]),
                "opp_result": _format_number(result["opp_result"]),
                "saved": saved,
            }
        else:
            _, mode, page = parsed
            response = _history_page(db, int(user["id"]), mode, page)
    except ValueError as exc:
        response = {"kind": "error", "ok": False, "message": str(exc)}
    except Exception:
        logging.getLogger("battle_widget").exception("Unexpected widget query error")
        response = {"kind": "error", "ok": False, "message": "حدث خطأ أثناء تنفيذ الطلب."}

    packed = _json_bytes(response)
    if len(packed) > 1024:
        packed = _json_bytes({"kind": "error", "ok": False, "message": "النتيجة أكبر من الحد المسموح."})
    api_response = await _telegram_api(token, "answerWidgetQuery", {
        "widget_query_id": query_id,
        "data": packed.decode("utf-8"),
    })
    if not api_response.get("ok"):
        logging.getLogger("battle_widget").warning("answerWidgetQuery failed: %s", api_response)
