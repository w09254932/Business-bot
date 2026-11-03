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

    def fake_write(owner_id, connection_id, owner_chat_id):
        calls.append((owner_id, connection_id, owner_chat_id))

    monkeypatch.setattr(
        firestore_store,
        "_write_connection_sync",
        fake_write,
    )

    asyncio.run(firestore_store.persist_business_connection({
        "id": "connection-A",
        "user": {"id": 1001},
        "user_chat_id": 2001,
        "is_enabled": True,
    }))

    assert calls == [(1001, "connection-A", 2001)]


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
