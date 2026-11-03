import asyncio

from app import main


def test_owner_text_reply_does_not_trigger_media_rescue(monkeypatch):
    connection_id = "owner-text-reply-test"
    owner_id = 99001

    main.CONNECTIONS[connection_id] = {
        "owner_id": owner_id,
        "owner_chat_id": owner_id,
        "enabled": True,
    }
    main.USER_SETTINGS[owner_id] = main.DEFAULT_USER_SETTINGS.copy()

    notifications = []

    async def fake_notify(chat_id, text):
        notifications.append(text)

    async def unexpected_rescue(*args, **kwargs):
        raise AssertionError("Text reply must not trigger media rescue")

    monkeypatch.setattr(main, "notify", fake_notify)
    monkeypatch.setattr(main, "rescue_reply_media", unexpected_rescue)

    try:
        asyncio.run(main.process_business_message({
            "business_connection_id": connection_id,
            "from": {"id": owner_id},
            "chat": {"id": 99002},
            "message_id": 300,
            "text": "رد صاحب الحساب",
            "reply_to_message": {
                "message_id": 200,
                "from": {"id": 99003},
                "chat": {"id": 99002},
                "text": "رسالة صديق عادية",
            },
        }))

        assert notifications == []
    finally:
        main.CONNECTIONS.pop(connection_id, None)
        main.USER_SETTINGS.pop(owner_id, None)


def test_owner_media_reply_without_fatha_prefix_is_ignored(monkeypatch):
    connection_id = "owner-media-without-fatha"
    owner_id = 81011

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

    rescue_calls = []
    notifications = []

    async def fake_rescue(*args, **kwargs):
        rescue_calls.append(args)

    async def fake_notify(*args):
        notifications.append(args)

    monkeypatch.setattr(main, "rescue_reply_media", fake_rescue)
    monkeypatch.setattr(main, "notify", fake_notify)

    try:
        asyncio.run(main.process_business_message({
            "business_connection_id": connection_id,
            "from": {"id": owner_id},
            "text": "حلوة الصورة \\u064e",
            "reply_to_message": {
                "message_id": 200,
                "from": {"id": 81012},
                "chat": {"id": 81013},
                "photo": [{"file_id": "customer-photo"}],
            },
        }))

        assert rescue_calls == []
        assert notifications == []
    finally:
        main.CONNECTIONS.pop(connection_id, None)
        main.USER_SETTINGS.pop(owner_id, None)
