import asyncio
import time

from app import main


def setup_owner(monkeypatch):
    owner_id = 9001

    monkeypatch.setitem(
        main.CONNECTIONS,
        "settings-test-connection",
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

    async def fake_persist(owner_id, settings):
        pass

    async def fake_notify(chat_id, text):
        pass

    monkeypatch.setattr(
        main.firestore_store,
        "persist_user_settings",
        fake_persist,
    )
    monkeypatch.setattr(main, "notify", fake_notify)

    return owner_id


def test_settings_message_has_real_line_breaks():
    text = main.format_owner_settings(main.DEFAULT_USER_SETTINGS)

    assert "\n" in text
    assert "\\n" not in text
    assert "إعدادات البوت" in text


def test_owner_can_enable_capture_any_media(monkeypatch):
    owner_id = setup_owner(monkeypatch)

    asyncio.run(main.process_private_message({
        "chat": {"id": owner_id, "type": "private"},
        "from": {"id": owner_id},
        "text": "/setting capture_any_media on",
    }))

    assert main.USER_SETTINGS[owner_id]["capture_any_media"] is True


def test_owner_can_change_retention(monkeypatch):
    owner_id = setup_owner(monkeypatch)

    asyncio.run(main.process_private_message({
        "chat": {"id": owner_id, "type": "private"},
        "from": {"id": owner_id},
        "text": "/retention 24",
    }))

    assert main.USER_SETTINGS[owner_id]["retention_hours"] == 24


def test_non_owner_cannot_change_settings(monkeypatch):
    owner_id = setup_owner(monkeypatch)

    asyncio.run(main.process_private_message({
        "chat": {"id": 7777, "type": "private"},
        "from": {"id": 7777},
        "text": "/setting capture_any_media on",
    }))

    assert main.USER_SETTINGS[owner_id]["capture_any_media"] is False
    assert 7777 not in main.USER_SETTINGS


def test_custom_retention_expires_owner_snapshot(monkeypatch):
    owner_id = setup_owner(monkeypatch)

    main.USER_SETTINGS[owner_id]["retention_hours"] = 1

    key = ("settings-test-connection", 55, 900)

    main.SNAPSHOTS[key] = {
        "text": "expired",
        "saved_at": time.monotonic() - 7200,
    }

    try:
        main.prune_memory()
        assert key not in main.SNAPSHOTS
    finally:
        main.SNAPSHOTS.pop(key, None)
