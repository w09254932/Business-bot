import asyncio

from app import main


def setup_owner(monkeypatch, owner_id):
    connection_id = f"quiet-save-{owner_id}"
    chat_id = owner_id + 1

    monkeypatch.setitem(
        main.CONNECTIONS,
        connection_id,
        {
            "owner_id": owner_id,
            "owner_chat_id": owner_id,
            "enabled": True,
        },
    )
    monkeypatch.setitem(
        main.USER_SETTINGS,
        owner_id,
        main.DEFAULT_USER_SETTINGS.copy(),
    )
    monkeypatch.setattr(main, "cache_enabled", lambda: False)

    saved = []
    notifications = []
    forwarded = []

    async def fake_persist(owner, connection, message, snapshot):
        saved.append(snapshot.copy())

    async def fake_notify(target, text):
        notifications.append(text)

    async def fake_send(target, media_type, file_id, *args):
        forwarded.append((target, media_type, file_id))

    monkeypatch.setattr(main, "persist_message_snapshot", fake_persist)
    monkeypatch.setattr(main, "notify", fake_notify)
    monkeypatch.setattr(main, "send_saved_media", fake_send)

    return connection_id, chat_id, saved, notifications, forwarded


def test_silent_settings_are_disabled_by_default():
    assert main.DEFAULT_USER_SETTINGS["notify_new_messages"] is False
    assert main.DEFAULT_USER_SETTINGS["auto_forward_media"] is False


def test_new_photo_is_saved_silently_without_forwarding(monkeypatch):
    owner_id = 98101
    connection_id, chat_id, saved, notifications, forwarded = setup_owner(
        monkeypatch, owner_id
    )
    key = (connection_id, chat_id, 101)

    try:
        asyncio.run(main.process_business_message({
            "business_connection_id": connection_id,
            "from": {"id": 77},
            "chat": {"id": chat_id},
            "message_id": 101,
            "photo": [{"file_id": "silent-photo"}],
        }))

        assert len(saved) == 1
        assert saved[0]["file_id"] == "silent-photo"
        assert notifications == []
        assert forwarded == []
    finally:
        main.forget_snapshot(*key)


def test_new_message_notification_can_be_enabled_alone(monkeypatch):
    owner_id = 98201
    connection_id, chat_id, saved, notifications, forwarded = setup_owner(
        monkeypatch, owner_id
    )
    main.USER_SETTINGS[owner_id]["notify_new_messages"] = True

    asyncio.run(main.process_business_message({
        "business_connection_id": connection_id,
        "from": {"id": 78},
        "chat": {"id": chat_id},
        "message_id": 102,
        "text": "رسالة اختبار",
    }))

    assert len(saved) == 1
    assert len(notifications) == 1
    assert "رسالة جديدة" in notifications[0]
    assert forwarded == []


def test_auto_forward_can_be_enabled_without_notification(monkeypatch):
    owner_id = 98301
    connection_id, chat_id, saved, notifications, forwarded = setup_owner(
        monkeypatch, owner_id
    )
    main.USER_SETTINGS[owner_id]["auto_forward_media"] = True
    key = (connection_id, chat_id, 103)

    try:
        asyncio.run(main.process_business_message({
            "business_connection_id": connection_id,
            "from": {"id": 79},
            "chat": {"id": chat_id},
            "message_id": 103,
            "photo": [{"file_id": "forward-enabled-photo"}],
        }))

        assert len(saved) == 1
        assert notifications == []
        assert forwarded == [(owner_id, "photo", "forward-enabled-photo")]
    finally:
        main.forget_snapshot(*key)
