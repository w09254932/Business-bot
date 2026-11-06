import asyncio

from app import main
from app.channel_archive import (
    publish_archive_message,
    publish_archive_notice,
)


def test_publish_message_and_photo(monkeypatch):
    monkeypatch.setenv("ARCHIVE_CHANNEL_ID", "-1002377381147")
    calls = []

    async def fake_telegram(method, payload):
        calls.append((method, payload))
        if method == "sendMessage":
            return {"message_id": 100, "chat": {"id": -1002377381147}}
        return {"message_id": 101, "chat": {"id": -1002377381147}}

    message = {
        "message_id": 12,
        "date": 1793860000,
        "from": {"id": 55, "first_name": "محمد"},
        "chat": {"id": 77, "type": "private"},
        "caption": "صورة تجريبية",
        "photo": [{"file_id": "photo-file"}],
    }

    result = asyncio.run(publish_archive_message(
        message, 999, "incoming", fake_telegram,
        media_type="photo",
        file_id="photo-file",
        reply_to_message_id=90,
    ))

    assert [call[0] for call in calls] == ["sendMessage", "sendPhoto"]
    assert calls[0][1]["reply_parameters"]["message_id"] == 90
    assert calls[1][1]["photo"] == "photo-file"
    assert calls[1][1]["reply_parameters"]["message_id"] == 100
    assert result["archive_message_id"] == 100
    assert result["archive_media_message_id"] == 101
    assert result["media_status"] == "sent"


def test_media_failure_keeps_archive_header(monkeypatch):
    monkeypatch.setenv("ARCHIVE_CHANNEL_ID", "-1002377381147")

    async def fake_telegram(method, payload):
        if method == "sendMessage":
            return {"message_id": 200, "chat": {"id": -1002377381147}}
        raise RuntimeError("media rejected")

    result = asyncio.run(publish_archive_message(
        {
            "message_id": 15,
            "date": 1793860000,
            "from": {"id": 55},
            "chat": {"id": 77, "type": "private"},
            "photo": [{"file_id": "bad-file"}],
        },
        999,
        "incoming",
        fake_telegram,
        media_type="photo",
        file_id="bad-file",
    ))

    assert result["archive_message_id"] == 200
    assert result["media_status"] == "failed"
    assert "media rejected" in result["media_error"]


def test_publish_notice_as_reply(monkeypatch):
    monkeypatch.setenv("ARCHIVE_CHANNEL_ID", "-1002377381147")
    captured = {}

    async def fake_telegram(method, payload):
        captured.update(payload)
        return {"message_id": 300, "chat": {"id": -1002377381147}}

    result = asyncio.run(publish_archive_notice(
        "تم حذف الرسالة & <أرشيف>",
        fake_telegram,
        reply_to_message_id=100,
    ))

    assert captured["reply_parameters"]["message_id"] == 100
    assert captured["text"] == "تم حذف الرسالة & <أرشيف>"
    assert result["archive_message_id"] == 300


def test_edited_message_replies_to_latest_archive_record(monkeypatch):
    captured = {}
    saved = []

    async def fake_load(*args):
        return {
            "first_archive_message_id": 40,
            "latest_archive_message_id": 50,
        }

    async def fake_publish(**kwargs):
        captured.update(kwargs)
        return {
            "archive_chat_id": -1002377381147,
            "archive_message_id": 60,
            "archive_media_message_id": None,
            "media_status": "not_applicable",
            "media_error": None,
            "direction": "incoming",
            "event": "edited",
        }

    async def fake_persist(owner, connection, record):
        saved.append((owner, connection, record.copy()))
        return True

    monkeypatch.setattr(
        main.firestore_store, "load_archive_record", fake_load
    )
    monkeypatch.setattr(main, "publish_archive_message", fake_publish)
    monkeypatch.setattr(
        main.firestore_store, "persist_archive_record", fake_persist
    )

    message = {
        "message_id": 70,
        "date": 1793860000,
        "from": {"id": 55},
        "chat": {"id": 77, "type": "private"},
        "text": "النص بعد التعديل",
    }

    result = asyncio.run(main._publish_business_archive(
        message, 999, "conn-A", "incoming", event="edited"
    ))

    assert captured["reply_to_message_id"] == 50
    assert captured["event"] == "edited"
    assert saved[0][0:2] == (999, "conn-A")
    assert saved[0][2]["message_id"] == 70
    assert result["archive_message_id"] == 60


def test_formatted_message_respects_telegram_length_limit():
    from app.channel_archive import format_archive_text

    message = {
        "message_id": 1,
        "date": 1793860000,
        "from": {"id": 2},
        "chat": {"id": 3, "type": "private"},
        "text": "&" * 3000,
    }

    result = format_archive_text(message, 999, "incoming")

    assert len(result) <= 3900
    assert result.endswith("…")
    assert not result.endswith("&")
