import asyncio

from app.database import firestore_store


def test_ram_mode_when_firestore_is_not_configured(monkeypatch):
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)

    assert firestore_store.is_configured() is False
    assert firestore_store.storage_mode() == "RAM only"
    assert asyncio.run(firestore_store.load_connections()) is None


def test_persist_enabled_connection(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")
    calls = []

    def fake_write(
        owner_id,
        connection_id,
        owner_chat_id,
        connection_data=None,
    ):
        calls.append((
            owner_id,
            connection_id,
            owner_chat_id,
            connection_data,
        ))

    monkeypatch.setattr(
        firestore_store,
        "_write_connection_sync",
        fake_write,
    )

    asyncio.run(firestore_store.persist_business_connection({
        "id": "connection-A",
        "user": {"id": 1001},
        "user_chat_id": 2001,
        "date": 1770000000,
        "rights": {
            "can_reply": True,
            "can_read_messages": True,
        },
        "is_enabled": True,
    }))

    assert calls[0][:3] == (1001, "connection-A", 2001)
    assert calls[0][3]["rights"]["can_reply"] is True
    assert calls[0][3]["date"] == 1770000000


def test_persist_disabled_connection(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")
    calls = []

    def fake_delete(owner_id, connection_id):
        calls.append((owner_id, connection_id))

    monkeypatch.setattr(
        firestore_store,
        "_delete_connection_sync",
        fake_delete,
    )

    asyncio.run(firestore_store.persist_business_connection({
        "id": "connection-B",
        "user": {"id": 1002},
        "user_chat_id": 2002,
        "is_enabled": False,
    }))

    assert calls == [(1002, "connection-B")]


def test_load_connections_from_store(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")

    expected = {
        "connection-C": {
            "owner_id": 1003,
            "owner_chat_id": 2003,
            "enabled": True,
        }
    }

    monkeypatch.setattr(
        firestore_store,
        "_load_connections_sync",
        lambda: expected,
    )

    assert asyncio.run(firestore_store.load_connections()) == expected


def test_invalid_connection_data_is_rejected(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")

    try:
        asyncio.run(
            firestore_store.persist_business_connection({
                "id": "connection-invalid",
                "user": {},
                "is_enabled": True,
            })
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid connection data was accepted")


def test_persist_message_snapshot_calls_store(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")
    calls = []

    def fake_write(owner_id, connection_id, snapshot):
        calls.append((owner_id, connection_id, snapshot.copy()))

    monkeypatch.setattr(
        firestore_store,
        "_write_message_snapshot_sync",
        fake_write,
    )

    snapshot = {
        "chat_id": 22,
        "message_id": 33,
        "text": "رسالة محفوظة",
        "media_type": "photo",
        "file_id": "photo-file",
    }

    result = asyncio.run(
        firestore_store.persist_message_snapshot(
            11,
            "connection-A",
            snapshot,
        )
    )

    assert result is True
    assert calls[0][0:2] == (11, "connection-A")
    assert calls[0][2]["file_id"] == "photo-file"


def test_persist_message_snapshot_requires_ids(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")

    try:
        asyncio.run(
            firestore_store.persist_message_snapshot(
                11,
                "connection-A",
                {"text": "missing identifiers"},
            )
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Incomplete snapshot was accepted")


def test_load_message_snapshot(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")

    expected = {
        "owner_id": 11,
        "chat_id": 22,
        "message_id": 33,
        "text": "persisted",
    }

    monkeypatch.setattr(
        firestore_store,
        "_read_message_snapshot_sync",
        lambda *args: expected,
    )

    result = asyncio.run(
        firestore_store.load_message_snapshot(
            11,
            "connection-A",
            22,
            33,
        )
    )

    assert result["text"] == "persisted"


def test_pop_message_snapshot(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")

    expected = {"text": "deleted snapshot"}

    monkeypatch.setattr(
        firestore_store,
        "_pop_message_snapshot_sync",
        lambda *args: expected,
    )

    result = asyncio.run(
        firestore_store.pop_message_snapshot(
            11,
            "connection-A",
            22,
            33,
        )
    )

    assert result == expected


def test_prune_message_snapshots(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")

    monkeypatch.setattr(
        firestore_store,
        "_prune_message_snapshots_sync",
        lambda *args: 7,
    )

    result = asyncio.run(
        firestore_store.prune_message_snapshots(
            11,
            "connection-A",
            6,
        )
    )

    assert result == 7
