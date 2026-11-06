import asyncio
from app import main


def test_archive_deletion_notice_is_linked_and_saved(monkeypatch):
    calls = {}

    async def fake_load(*args):
        return {
            "latest_archive_message_id": 77,
            "first_archive_message_id": 70,
            "is_deleted": False,
        }

    async def fake_publish(**kwargs):
        calls.update(kwargs)
        return {
            "archive_chat_id": -100123,
            "archive_message_id": 88,
        }

    async def fake_mark(*args):
        calls["mark"] = args

    monkeypatch.setattr(
        main.firestore_store, "load_archive_record", fake_load
    )
    monkeypatch.setattr(main, "publish_archive_notice", fake_publish)
    monkeypatch.setattr(
        main.firestore_store, "mark_archive_deleted", fake_mark
    )

    result = asyncio.run(
        main._publish_business_archive_deletion(
            11, "conn-A", 22, 33
        )
    )

    assert calls["reply_to_message_id"] == 77
    assert "تم حذف الرسالة الأصلية" in calls["text"]
    assert calls["mark"] == (11, "conn-A", 22, 33, 88)
    assert result["archive_message_id"] == 88


def test_deleted_archive_record_does_not_publish_duplicate(monkeypatch):
    calls = []

    async def fake_load(*args):
        return {"is_deleted": True}

    async def fake_publish(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        main.firestore_store, "load_archive_record", fake_load
    )
    monkeypatch.setattr(main, "publish_archive_notice", fake_publish)

    result = asyncio.run(
        main._publish_business_archive_deletion(
            11, "conn-A", 22, 33
        )
    )

    assert result is None
    assert calls == []


def test_missing_archive_record_does_not_publish(monkeypatch):
    calls = []

    async def fake_load(*args):
        return None

    async def fake_publish(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        main.firestore_store, "load_archive_record", fake_load
    )
    monkeypatch.setattr(main, "publish_archive_notice", fake_publish)

    result = asyncio.run(
        main._publish_business_archive_deletion(
            11, "conn-A", 22, 33
        )
    )

    assert result is None
    assert calls == []
