import asyncio
import time

from app import main


def connection_fixture():
    return {
        "owner_id": 81001,
        "owner_chat_id": 81001,
        "enabled": True,
    }


def test_snapshot_restores_from_firestore(monkeypatch):
    connection = connection_fixture()
    key = ("persistent-connection", 55, 100)

    main.forget_snapshot(*key)

    async def fake_load(*args):
        return {
            "text": "رسالة دائمة",
            "media_type": "photo",
            "file_id": "saved-photo-id",
            "file_unique_id": "unique-photo",
            "saved_at_unix": int(time.time()),
        }

    monkeypatch.setattr(
        main.firestore_store,
        "load_message_snapshot",
        fake_load,
    )

    try:
        result = asyncio.run(
            main.get_snapshot_with_firestore(
                key[0],
                key[1],
                key[2],
                connection,
            )
        )

        assert result is not None
        assert result["text"] == "رسالة دائمة"
        assert result["file_id"] == "saved-photo-id"
        assert result["file_path"] is None
    finally:
        main.forget_snapshot(*key)


def test_missing_firestore_snapshot_returns_none(monkeypatch):
    async def fake_load(*args):
        return None

    monkeypatch.setattr(
        main.firestore_store,
        "load_message_snapshot",
        fake_load,
    )

    result = asyncio.run(
        main.get_snapshot_with_firestore(
            "unknown-connection",
            55,
            101,
            connection_fixture(),
        )
    )

    assert result is None


def test_new_message_is_persisted_to_firestore(monkeypatch):
    connection_id = "persistent-connection-test"
    chat_id = 81002
    message_id = 81003
    owner_id = 81001

    main.CONNECTIONS[connection_id] = connection_fixture()
    main.USER_SETTINGS[owner_id] = main.DEFAULT_USER_SETTINGS.copy()

    saved = []

    async def fake_persist(owner, connection, message, snapshot):
        saved.append({
            "owner": owner,
            "connection": connection,
            "message": message.copy(),
            "snapshot": snapshot.copy(),
        })

    async def fake_notify(*args):
        pass

    async def fake_send(*args):
        pass

    monkeypatch.setattr(
        main,
        "persist_message_snapshot",
        fake_persist,
    )
    monkeypatch.setattr(main, "notify", fake_notify)
    monkeypatch.setattr(main, "send_saved_media", fake_send)
    monkeypatch.setattr(main, "cache_enabled", lambda: False)

    key = (connection_id, chat_id, message_id)

    try:
        asyncio.run(main.process_business_message({
            "business_connection_id": connection_id,
            "from": {"id": 82001},
            "chat": {"id": chat_id},
            "message_id": message_id,
            "text": "رسالة للاختبار",
        }))

        assert len(saved) == 1
        assert saved[0]["owner"] == owner_id
        assert saved[0]["snapshot"]["text"] == "رسالة للاختبار"
    finally:
        main.forget_snapshot(*key)
        main.USER_SETTINGS.pop(owner_id, None)
        main.CONNECTIONS.pop(connection_id, None)


def test_deleted_message_falls_back_to_firestore(monkeypatch):
    connection_id = "persistent-delete-test"
    chat_id = 83001
    message_id = 83002

    main.CONNECTIONS[connection_id] = connection_fixture()
    key = (connection_id, chat_id, message_id)
    main.forget_snapshot(*key)

    notifications = []
    deleted = []

    async def fake_load(owner, connection, chat, message):
        return {
            "text": "محفوظ في Firestore",
            "media_type": None,
            "file_id": None,
            "saved_at_unix": int(time.time()),
        }

    async def fake_delete(*args):
        deleted.append(args)

    async def fake_notify(chat_id, text):
        notifications.append(text)

    monkeypatch.setattr(
        main.firestore_store,
        "load_message_snapshot",
        fake_load,
    )
    monkeypatch.setattr(
        main.firestore_store,
        "delete_message_snapshot",
        fake_delete,
    )
    monkeypatch.setattr(main, "notify", fake_notify)

    try:
        asyncio.run(main.process_deleted_messages({
            "business_connection_id": connection_id,
            "chat": {"id": chat_id},
            "message_ids": [message_id],
        }))

        assert any(
            "محفوظ في Firestore" in text
            for text in notifications
        )
        assert len(deleted) == 1
        assert main.get_snapshot(*key) is None
    finally:
        main.forget_snapshot(*key)
        main.CONNECTIONS.pop(connection_id, None)
