import asyncio

from app import main


def setup_connection(monkeypatch):
    monkeypatch.setitem(
        main.CONNECTIONS,
        "lifecycle-connection",
        {
            "owner_id": 99,
            "owner_chat_id": 100,
            "enabled": True,
        },
    )


def test_edited_message_preserves_old_and_new_text(monkeypatch):
    setup_connection(monkeypatch)
    key = ("lifecycle-connection", 55, 700)

    main.remember_snapshot(
        "lifecycle-connection",
        {
            "chat": {"id": 55},
            "message_id": 700,
            "text": "النص القديم",
        },
    )

    notifications = []

    async def fake_notify(chat_id, text):
        notifications.append(text)

    monkeypatch.setattr(main, "notify", fake_notify)

    try:
        asyncio.run(main.process_business_message(
            {
                "business_connection_id": "lifecycle-connection",
                "from": {"id": 7},
                "chat": {"id": 55},
                "message_id": 700,
                "text": "النص الجديد",
            },
            edited=True,
        ))

        assert any("النص القديم" in item for item in notifications)
        assert any("النص الجديد" in item for item in notifications)
        assert main.get_snapshot(*key)["text"] == "النص الجديد"
    finally:
        main.forget_snapshot(*key)


def test_deleted_message_restores_saved_photo(monkeypatch):
    setup_connection(monkeypatch)
    key = ("lifecycle-connection", 55, 701)

    main.remember_snapshot(
        "lifecycle-connection",
        {
            "chat": {"id": 55},
            "message_id": 701,
            "text": "صورة محفوظة مؤقتاً",
            "photo": [{"file_id": "saved-photo"}],
        },
    )

    sent = []
    notifications = []

    async def fake_send(chat_id, media_type, file_id):
        sent.append((chat_id, media_type, file_id))

    async def fake_notify(chat_id, text):
        notifications.append(text)

    monkeypatch.setattr(main, "send_saved_media", fake_send)
    monkeypatch.setattr(main, "notify", fake_notify)

    try:
        asyncio.run(main.process_deleted_messages({
            "business_connection_id": "lifecycle-connection",
            "chat": {"id": 55},
            "message_ids": [701],
        }))

        assert sent == [(100, "photo", "saved-photo")]
        assert any(
            "صورة محفوظة مؤقتاً" in item
            for item in notifications
        )
        assert main.get_snapshot(*key) is None
    finally:
        main.forget_snapshot(*key)


def test_deleted_message_without_snapshot_notifies_owner(monkeypatch):
    setup_connection(monkeypatch)
    key = ("lifecycle-connection", 55, 702)
    main.forget_snapshot(*key)

    notifications = []

    async def fake_notify(chat_id, text):
        notifications.append((chat_id, text))

    monkeypatch.setattr(main, "notify", fake_notify)

    asyncio.run(main.process_deleted_messages({
        "business_connection_id": "lifecycle-connection",
        "chat": {"id": 55},
        "message_ids": [702],
    }))

    assert len(notifications) == 1
    assert notifications[0][0] == 100
    assert "تم حذف رسالة" in notifications[0][1]


def test_business_connection_can_be_disabled():
    connection_id = "connection-to-disable"

    main.CONNECTIONS[connection_id] = {
        "owner_id": 99,
        "owner_chat_id": 100,
        "enabled": True,
    }

    main.save_business_connection({
        "id": connection_id,
        "user": {"id": 99},
        "user_chat_id": 100,
        "is_enabled": False,
    })

    assert connection_id not in main.CONNECTIONS
