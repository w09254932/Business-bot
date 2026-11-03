import asyncio
from app import main


def test_unconnected_user_sees_welcome(monkeypatch):
    owner_id = 700001
    monkeypatch.setattr(main, "CONNECTIONS", {})
    calls = []

    async def fake_telegram(method, payload):
        calls.append((method, payload))

    monkeypatch.setattr(main, "telegram", fake_telegram)

    asyncio.run(main.process_private_message({
        "chat": {"id": owner_id, "type": "private"},
        "from": {"id": owner_id},
        "text": "/start",
    }))

    assert calls[0][0] == "sendMessage"
    button = calls[0][1]["reply_markup"]["inline_keyboard"][0][0]
    assert button["url"] == "tg://settings/business"
    assert "اربط حساب" in calls[0][1]["text"]


def test_connected_user_sees_personal_settings(monkeypatch):
    owner_id = 700002
    monkeypatch.setattr(main, "CONNECTIONS", {
        "connection-user-2": {
            "owner_id": owner_id,
            "owner_chat_id": owner_id,
            "enabled": True,
        }
    })
    monkeypatch.setitem(
        main.USER_SETTINGS,
        owner_id,
        main.DEFAULT_USER_SETTINGS.copy(),
    )

    calls = []

    async def fake_telegram(method, payload):
        calls.append((method, payload))

    monkeypatch.setattr(main, "telegram", fake_telegram)

    asyncio.run(main.process_private_message({
        "chat": {"id": owner_id, "type": "private"},
        "from": {"id": owner_id},
        "text": "/settings",
    }))

    assert calls[0][0] == "sendMessage"
    assert calls[0][1]["reply_markup"]["inline_keyboard"]


def test_connection_activation_saves_and_confirms(monkeypatch):
    owner_id = 700003
    monkeypatch.setattr(main, "CONNECTIONS", {})
    settings = main.DEFAULT_USER_SETTINGS.copy()
    saved = []
    notifications = []
    menus = []

    async def fake_persist_connection(data):
        saved.append(data.copy())

    async def fake_get_settings(user_id):
        assert user_id == owner_id
        return settings.copy()

    async def fake_persist_settings(user_id, value):
        pass

    async def fake_notify(chat_id, text):
        notifications.append((chat_id, text))

    async def fake_menu(chat_id, value):
        menus.append((chat_id, value))

    monkeypatch.setattr(
        main.firestore_store,
        "persist_business_connection",
        fake_persist_connection,
    )
    monkeypatch.setattr(main, "get_owner_settings", fake_get_settings)
    monkeypatch.setattr(
        main.firestore_store,
        "persist_user_settings",
        fake_persist_settings,
    )
    monkeypatch.setattr(main, "notify", fake_notify)
    monkeypatch.setattr(main, "send_settings_menu", fake_menu)

    data = {
        "id": "connection-user-3",
        "user": {
            "id": owner_id,
            "first_name": "Test",
            "username": "test_user",
        },
        "user_chat_id": owner_id,
        "date": 1770000000,
        "is_enabled": True,
        "rights": {"can_reply": True},
    }

    asyncio.run(main.handle_update({"business_connection": data}))

    connection = main.CONNECTIONS["connection-user-3"]

    assert saved
    assert connection["owner_id"] == owner_id
    assert connection["rights"]["can_reply"] is True
    assert connection["business_user"]["username"] == "test_user"
    assert any("بنجاح" in text for _, text in notifications)
    assert menus[0][0] == owner_id


def test_disconnect_removes_only_that_connection(monkeypatch):
    owner_id = 700004
    other_owner = 700005

    monkeypatch.setattr(main, "CONNECTIONS", {
        "disconnect-me": {
            "owner_id": owner_id,
            "owner_chat_id": owner_id,
            "enabled": True,
        },
        "keep-this": {
            "owner_id": other_owner,
            "owner_chat_id": other_owner,
            "enabled": True,
        },
    })
    monkeypatch.setattr(main, "SNAPSHOTS", {
        ("disconnect-me", 10, 20): {
            "file_path": None,
            "saved_at": 9999999999,
        }
    })

    async def fake_persist(data):
        pass

    notifications = []
    welcomes = []

    async def fake_notify(chat_id, text):
        notifications.append((chat_id, text))

    async def fake_welcome(chat_id):
        welcomes.append(chat_id)

    monkeypatch.setattr(
        main.firestore_store,
        "persist_business_connection",
        fake_persist,
    )
    monkeypatch.setattr(main, "notify", fake_notify)
    monkeypatch.setattr(main, "send_welcome_menu", fake_welcome)

    asyncio.run(main.handle_update({
        "business_connection": {
            "id": "disconnect-me",
            "user": {"id": owner_id},
            "user_chat_id": owner_id,
            "is_enabled": False,
        }
    }))

    assert "disconnect-me" not in main.CONNECTIONS
    assert "keep-this" in main.CONNECTIONS
    assert ("disconnect-me", 10, 20) not in main.SNAPSHOTS
    assert welcomes == [owner_id]
    assert any("فصل" in text for _, text in notifications)


def test_two_users_have_separate_settings(monkeypatch):
    first, second = 700006, 700007

    monkeypatch.setattr(main, "CONNECTIONS", {
        "connection-first": {
            "owner_id": first,
            "owner_chat_id": first,
            "enabled": True,
        },
        "connection-second": {
            "owner_id": second,
            "owner_chat_id": second,
            "enabled": True,
        },
    })

    monkeypatch.setattr(main, "USER_SETTINGS", {
        first: main.DEFAULT_USER_SETTINGS.copy(),
        second: main.DEFAULT_USER_SETTINGS.copy(),
    })

    async def fake_save(owner_id, settings):
        main.USER_SETTINGS[owner_id] = settings.copy()

    async def fake_telegram(method, payload):
        return {}

    monkeypatch.setattr(main, "save_owner_settings", fake_save)
    monkeypatch.setattr(main, "telegram", fake_telegram)

    asyncio.run(main.process_callback_query({
        "id": "callback-first",
        "from": {"id": first},
        "data": "settings:toggle:rescue_photo",
        "message": {
            "message_id": 55,
            "chat": {"id": first},
        },
    }))

    assert main.USER_SETTINGS[first]["rescue_photo"] is False
    assert main.USER_SETTINGS[second]["rescue_photo"] is True
