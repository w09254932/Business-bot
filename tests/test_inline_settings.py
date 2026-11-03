import asyncio

from app import main


def setup_owner(monkeypatch, owner_id=99101):
    connection_id = f"buttons-{owner_id}"

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

    calls = []
    saved = []

    async def fake_telegram(method, payload):
        calls.append((method, payload))
        return {"message_id": 50}

    async def fake_save(owner, settings):
        saved.append((owner, settings.copy()))
        main.USER_SETTINGS[owner] = settings.copy()

    monkeypatch.setattr(main, "telegram", fake_telegram)
    monkeypatch.setattr(main, "save_owner_settings", fake_save)

    return connection_id, calls, saved


def callback(owner_id, data):
    return {
        "id": "callback-test",
        "from": {"id": owner_id},
        "data": data,
        "message": {
            "message_id": 50,
            "chat": {"id": owner_id, "type": "private"},
        },
    }


def test_inline_button_toggles_setting(monkeypatch):
    owner_id = 99101
    _, calls, saved = setup_owner(monkeypatch, owner_id)

    asyncio.run(main.process_callback_query(
        callback(owner_id, "settings:toggle:rescue_photo")
    ))

    assert saved[-1][0] == owner_id
    assert saved[-1][1]["rescue_photo"] is False
    assert calls[-1][0] == "editMessageText"


def test_inline_button_changes_retention(monkeypatch):
    owner_id = 99102
    _, calls, saved = setup_owner(monkeypatch, owner_id)

    asyncio.run(main.process_callback_query(
        callback(owner_id, "settings:retention:24")
    ))

    assert saved[-1][1]["retention_hours"] == 24
    assert calls[-1][0] == "editMessageText"


def test_other_user_cannot_control_settings(monkeypatch):
    owner_id = 99103
    _, calls, saved = setup_owner(monkeypatch, owner_id)

    asyncio.run(main.process_callback_query(
        callback(99199, "settings:toggle:rescue_photo")
    ))

    assert saved == []
    assert calls[-1][0] == "answerCallbackQuery"
    assert calls[-1][1]["show_alert"] is True
