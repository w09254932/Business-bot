import asyncio
import pytest
from app.database import firestore_store


def test_persist_archive_record(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")
    calls = []

    def fake_write(owner, connection, record):
        calls.append((owner, connection, record.copy()))

    monkeypatch.setattr(
        firestore_store, "_write_archive_record_sync", fake_write
    )

    record = {
        "chat_id": 22,
        "message_id": 33,
        "archive_chat_id": -100123,
        "archive_message_id": 44,
        "direction": "incoming",
        "event": "new",
    }

    result = asyncio.run(
        firestore_store.persist_archive_record(11, "conn-A", record)
    )

    assert result is True
    assert calls[0][0:2] == (11, "conn-A")
    assert calls[0][2]["archive_message_id"] == 44


def test_persist_archive_record_requires_identifiers(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")

    with pytest.raises(ValueError):
        asyncio.run(
            firestore_store.persist_archive_record(
                11, "conn-A", {"chat_id": 22}
            )
        )


def test_load_archive_record(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")
    expected = {"archive_message_id": 44}

    monkeypatch.setattr(
        firestore_store,
        "_read_archive_record_sync",
        lambda *args: expected,
    )

    result = asyncio.run(
        firestore_store.load_archive_record(11, "conn-A", 22, 33)
    )

    assert result == expected


def test_mark_archive_deleted(monkeypatch):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{}")
    calls = []

    def fake_mark(*args):
        calls.append(args)
        return True

    monkeypatch.setattr(
        firestore_store, "_mark_archive_deleted_sync", fake_mark
    )

    result = asyncio.run(
        firestore_store.mark_archive_deleted(
            11, "conn-A", 22, 33, 55
        )
    )

    assert result is True
    assert calls == [(11, "conn-A", 22, 33, 55)]


def test_archive_storage_disabled_without_firestore(monkeypatch):
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)

    assert asyncio.run(
        firestore_store.persist_archive_record(
            11, "conn-A",
            {
                "chat_id": 22,
                "message_id": 33,
                "archive_chat_id": -100123,
                "archive_message_id": 44,
            },
        )
    ) is False

    assert asyncio.run(
        firestore_store.load_archive_record(11, "conn-A", 22, 33)
    ) is None

    assert asyncio.run(
        firestore_store.mark_archive_deleted(11, "conn-A", 22, 33)
    ) is False
