import asyncio
from app import main


def setup_owner(monkeypatch, connection_id, owner_id):
    monkeypatch.setitem(main.CONNECTIONS, connection_id, {
        "owner_id": owner_id,
        "owner_chat_id": owner_id,
        "enabled": True,
    })
    monkeypatch.setitem(
        main.USER_SETTINGS, owner_id,
        main.DEFAULT_USER_SETTINGS.copy()
    )


def test_owner_outgoing_message_is_archived(monkeypatch):
    connection_id, owner_id = "archive-outgoing-test", 90101
    setup_owner(monkeypatch, connection_id, owner_id)
    calls = []

    async def fake_archive(message, owner, connection, direction, event="new"):
        calls.append((owner, connection, direction, event))

    monkeypatch.setattr(main, "_publish_business_archive", fake_archive)

    asyncio.run(main.process_business_message({
        "business_connection_id": connection_id,
        "from": {"id": owner_id},
        "chat": {"id": 90102, "type": "private"},
        "message_id": 90103,
        "text": "رسالة صادرة للاختبار",
    }))

    assert calls == [(owner_id, connection_id, "outgoing", "new")]


def test_fatha_rescue_command_is_not_archived(monkeypatch):
    connection_id, owner_id = "archive-fatha-test", 90201
    setup_owner(monkeypatch, connection_id, owner_id)
    calls = []

    async def fake_archive(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(main, "_publish_business_archive", fake_archive)

    asyncio.run(main.process_business_message({
        "business_connection_id": connection_id,
        "from": {"id": owner_id},
        "chat": {"id": 90202, "type": "private"},
        "message_id": 90203,
        "text": "\u064eاستعادة",
        "reply_to_message": {
            "message_id": 90204,
            "from": {"id": 90205},
            "chat": {"id": 90202, "type": "private"},
            "text": "رسالة نصية فقط",
        },
    }))

    assert calls == []
