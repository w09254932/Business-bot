import asyncio

from app import main


def setup_connection(monkeypatch):
    monkeypatch.setitem(
        main.CONNECTIONS,
        "test-connection",
        {
            "owner_id": 99,
            "owner_chat_id": 100,
            "enabled": True,
        },
    )


def test_owner_reply_resends_accessible_photo(monkeypatch):
    setup_connection(monkeypatch)

    sent = []
    notifications = []

    async def fake_send(chat_id, media_type, file_id):
        sent.append((chat_id, media_type, file_id))

    async def fake_notify(chat_id, text):
        notifications.append((chat_id, text))

    monkeypatch.setattr(main, "send_saved_media", fake_send)
    monkeypatch.setattr(main, "notify", fake_notify)

    async def scenario():
        await main.process_business_message({
            "business_connection_id": "test-connection",
            "from": {"id": 99},
            "reply_to_message": {
                "from": {"id": 7},
                "photo": [{"file_id": "photo-123"}],
                "view_once": True,
            },
        })

    asyncio.run(scenario())

    assert sent == [(100, "photo", "photo-123")]
    assert any("بنجاح" in text for _, text in notifications)


def test_owner_text_reply_is_ignored(monkeypatch):
    setup_connection(monkeypatch)

    notifications = []

    async def fake_notify(chat_id, text):
        notifications.append(text)

    async def unexpected_send(*args):
        raise AssertionError("No media should be sent")

    monkeypatch.setattr(main, "notify", fake_notify)
    monkeypatch.setattr(main, "send_saved_media", unexpected_send)

    async def scenario():
        await main.process_business_message({
            "business_connection_id": "test-connection",
            "from": {"id": 99},
            "reply_to_message": {
                "from": {"id": 7},
                "text": "No media",
            },
        })

    asyncio.run(scenario())

    assert notifications == []


def test_incoming_customer_photo_still_works(monkeypatch):
    setup_connection(monkeypatch)

    sent = []
    notifications = []

    async def fake_send(chat_id, media_type, file_id):
        sent.append((chat_id, media_type, file_id))

    async def fake_notify(chat_id, text):
        notifications.append(text)

    monkeypatch.setattr(main, "send_saved_media", fake_send)
    monkeypatch.setattr(main, "notify", fake_notify)

    async def scenario():
        await main.process_business_message({
            "business_connection_id": "test-connection",
            "from": {"id": 7},
            "chat": {"id": 55},
            "message_id": 77,
            "photo": [{"file_id": "customer-photo"}],
        })

    try:
        asyncio.run(scenario())

        assert sent == [(100, "photo", "customer-photo")]
        assert any("رسالة جديدة" in text for text in notifications)
        assert main.get_snapshot("test-connection", 55, 77) is not None
    finally:
        main.forget_snapshot("test-connection", 55, 77)
