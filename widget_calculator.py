# -*- coding: utf-8 -*-
"""Telegram Widget protocol, validation and Firestore history helpers."""
from __future__ import annotations

import hashlib
import json
import logging
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from battle_core import MODE_LABELS, compute_battle

LOGGER = logging.getLogger("tr_battle.widget")
WIDGET_HTML_PATH = Path(__file__).with_name("widget.html")
VALID_MODES = set(MODE_LABELS)
PAGE_SIZE = 5
MAX_NUMBER_DIGITS = 15


def load_widget_html() -> str:
    html = WIDGET_HTML_PATH.read_text(encoding="utf-8")
    raw = html.encode("utf-8")
    compressed = zlib.compress(raw, level=9)
    if b"\x00" in raw:
        raise ValueError("Widget HTML contains a NUL byte")
    if len(raw) > 32_768:
        raise ValueError(f"Widget HTML is too large: {len(raw)} bytes (max 32768)")
    if len(compressed) > 4_096:
        raise ValueError(f"Compressed Widget HTML is too large: {len(compressed)} bytes (max 4096)")
    return html


def parse_widget_request(data: str) -> tuple:
    if not isinstance(data, str) or not data or len(data.encode("utf-8")) > 64:
        raise ValueError("بيانات الطلب غير صالحة")
    parts = data.split("|")
    if len(parts) == 4 and parts[0] == "calc":
        _, mode, raw_mine, raw_opp = parts
        if mode not in VALID_MODES:
            raise ValueError("نوع المعركة غير صالح")
        values = (raw_mine, raw_opp)
        if any(not value or not value.isascii() or not value.isdigit() for value in values):
            raise ValueError("أدخل أرقامًا صحيحة فقط")
        if any(len(value) > MAX_NUMBER_DIGITS for value in values):
            raise ValueError("الحد الأقصى 15 خانة لكل رقم")
        return "calc", mode, int(raw_mine), int(raw_opp)

    if len(parts) == 3 and parts[0] == "history":
        _, mode, raw_page = parts
        if mode not in VALID_MODES | {"all"}:
            raise ValueError("نوع السجل غير صالح")
        if not raw_page or not raw_page.isascii() or not raw_page.isdigit():
            raise ValueError("رقم الصفحة غير صالح")
        page = int(raw_page)
        if page > 10_000:
            raise ValueError("رقم الصفحة خارج النطاق")
        return "history", mode, page

    raise ValueError("الطلب غير معروف")


def format_number(value: Any) -> str:
    try:
        number = float(value or 0)
        return str(int(number)) if number.is_integer() else f"{number:.1f}"
    except (TypeError, ValueError, OverflowError):
        return "0"


def _history_timestamp(value: Any) -> str:
    if not hasattr(value, "astimezone"):
        return "—"
    try:
        return value.astimezone(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError):
        return "—"


def get_history_page(db: Any, user_id: int, mode: str = "all", page: int = 0) -> dict[str, Any]:
    """Return a page of the requesting user's own battles only."""
    if db is None:
        return {"kind": "history", "ok": False, "message": "السجل غير مفعّل. أضف إعدادات Firestore أولاً."}
    try:
        query = db.collection("battles").where("user_id", "==", int(user_id))
        if mode != "all":
            query = query.where("mode", "==", mode)
        query = query.order_by("ts", direction="DESCENDING")
        docs = list(query.offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1).stream())
        has_more = len(docs) > PAGE_SIZE
        docs = docs[:PAGE_SIZE]
        rows = []
        for doc in docs:
            row = doc.to_dict() or {}
            rows.append({
                "mode": row.get("mode", "individual"),
                "result": str(row.get("result", "—")),
                "mine": int(row.get("my_number", 0) or 0),
                "opponent": int(row.get("opp_number", 0) or 0),
                "points": format_number(row.get("my_result", 0)),
                "date": _history_timestamp(row.get("ts")),
            })
        return {"kind": "history", "ok": True, "rows": rows,
                "page": page, "has_more": has_more, "mode": mode}
    except Exception:
        LOGGER.exception("Could not read battle history")
        return {"kind": "history", "ok": False, "message": "تعذّر جلب السجل الآن."}


def save_battle_once(db: Any, query_id: str, user: dict, mode: str,
                     mine: int, opponent: int, result: dict) -> bool:
    """Persist a battle forever; query id prevents duplicate webhook writes."""
    if db is None:
        return False
    user_id = user.get("id")
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        return False

    digest = hashlib.sha256(query_id.encode("utf-8")).hexdigest()
    document = db.collection("battles").document(f"widget_{digest}")
    full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])).strip()
    record = {
        "user_id": user_id,
        "username": user.get("username"),
        "name": full_name or user.get("first_name") or "",
        "mode": mode,
        "source": "widget",
        "my_number": mine,
        "my_points": result["my_points"],
        "opp_number": opponent,
        "opp_points": result["opp_points"],
        "result": result["result_label"],
        "my_result": result["my_result"],
        "opp_result": result["opp_result"],
        "ts": datetime.now(timezone.utc),
        "widget_query_id": query_id,
    }
    try:
        document.create(record)
    except Exception as exc:
        if exc.__class__.__name__ == "AlreadyExists":
            return True
        LOGGER.exception("Could not save battle to Firestore")
        return False

    try:
        from firebase_admin import firestore
        db.collection("stats").document("counters").set({
            "total": firestore.Increment(1),
            f"mode_{mode}": firestore.Increment(1),
        }, merge=True)
    except Exception:
        LOGGER.exception("Battle saved but counters were not updated")
    return True


def encode_widget_answer(data: dict[str, Any]) -> str:
    packed = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(packed.encode("utf-8")) > 1_024:
        packed = json.dumps({"kind": "error", "ok": False,
                             "message": "النتيجة أكبر من الحد المسموح."},
                            ensure_ascii=False, separators=(",", ":"))
    return packed
