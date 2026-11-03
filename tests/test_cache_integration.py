import asyncio

from app import main


def test_file_id_failure_uses_cached_file(monkeypatch, tmp_path):
    cached_file = tmp_path / "photo.jpg"
    cached_file.write_bytes(b"cached-image")

    calls = []
    uploads = []

    async def fake_telegram(method, payload):
        calls.append((method, payload))
        raise RuntimeError("Telegram file reference rejected")

    async def fake_upload(token, chat_id, media_type, file_path):
        uploads.append((token, chat_id, media_type, file_path))
        return {"message_id": 500}

    monkeypatch.setattr(main, "telegram", fake_telegram)
    monkeypatch.setattr(main, "upload_cached_media", fake_upload)
    monkeypatch.setattr(main, "BOT_TOKEN", "test-token")

    result = asyncio.run(
        main.send_saved_media(
            100,
            "photo",
            "expired-file-id",
            str(cached_file),
        )
    )

    assert result == {"message_id": 500}
    assert len(calls) == 1
    assert uploads == [
        ("test-token", 100, "photo", str(cached_file))
    ]


def test_file_id_success_does_not_upload_cached_file(
    monkeypatch, tmp_path
):
    cached_file = tmp_path / "photo.jpg"
    cached_file.write_bytes(b"cached-image")

    async def fake_telegram(method, payload):
        return {"message_id": 501}

    async def unexpected_upload(*args):
        raise AssertionError("Cached upload should not be called")

    monkeypatch.setattr(main, "telegram", fake_telegram)
    monkeypatch.setattr(main, "upload_cached_media", unexpected_upload)

    result = asyncio.run(
        main.send_saved_media(
            100,
            "photo",
            "valid-file-id",
            str(cached_file),
        )
    )

    assert result == {"message_id": 501}


def test_file_id_failure_without_cache_propagates_error(monkeypatch):
    async def fake_telegram(method, payload):
        raise RuntimeError("Telegram rejected file")

    monkeypatch.setattr(main, "telegram", fake_telegram)

    try:
        asyncio.run(
            main.send_saved_media(
                100,
                "photo",
                "bad-file-id",
            )
        )
    except RuntimeError as error:
        assert "rejected" in str(error)
    else:
        raise AssertionError("Expected Telegram error")


def test_incoming_ordinary_photo_is_cached(monkeypatch):
    connection_id = "cache-test-connection"
    owner_id = 91001
    chat_id = 91002
    message_id = 91003

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

    cache_calls = []
    forwarded = []

    monkeypatch.setattr(main, "cache_enabled", lambda: True)
    monkeypatch.setattr(main, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(main, "is_one_time", lambda message: False)

    async def fake_cache(*args, **kwargs):
        cache_calls.append(args)
        return "/cache/photo.jpg"

    async def fake_send(*args):
        forwarded.append(args)

    async def fake_notify(*args):
        pass

    monkeypatch.setattr(main, "cache_telegram_media", fake_cache)
    monkeypatch.setattr(main, "send_saved_media", fake_send)
    monkeypatch.setattr(main, "notify", fake_notify)

    key = (connection_id, chat_id, message_id)

    try:
        asyncio.run(main.process_business_message({
            "business_connection_id": connection_id,
            "from": {"id": 77},
            "chat": {"id": chat_id},
            "message_id": message_id,
            "photo": [{
                "file_id": "ordinary-photo",
                "file_unique_id": "ordinary-unique",
                "file_size": 100,
            }],
        }))

        snapshot = main.get_snapshot(*key)

        assert len(cache_calls) == 1
        assert snapshot["file_path"] == "/cache/photo.jpg"
        assert forwarded
    finally:
        main.forget_snapshot(*key)


def test_marked_one_time_media_is_cached_on_arrival(monkeypatch):
    connection_id = "one-time-cache-test"
    owner_id = 92001
    chat_id = 92002
    message_id = 92003

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

    cache_calls = []
    monkeypatch.setattr(main, "cache_enabled", lambda: True)
    monkeypatch.setattr(main, "BOT_TOKEN", "test-token")

    async def fake_cache(*args, **kwargs):
        cache_calls.append(args)
        return "/cache/temporary.jpg"

    async def fake_send(*args):
        pass

    async def fake_notify(*args):
        pass

    monkeypatch.setattr(main, "cache_telegram_media", fake_cache)
    monkeypatch.setattr(main, "send_saved_media", fake_send)
    monkeypatch.setattr(main, "notify", fake_notify)

    key = (connection_id, chat_id, message_id)

    try:
        asyncio.run(main.process_business_message({
            "business_connection_id": connection_id,
            "from": {"id": 78},
            "chat": {"id": chat_id},
            "message_id": message_id,
            "photo": [{"file_id": "temporary-photo"}],
            "view_once": True,
        }))

        assert cache_calls == [
            ("test-token", "temporary-photo", None, None)
        ]
        snapshot = main.get_snapshot(*key)
        assert snapshot["file_path"] == "/cache/temporary.jpg"
    finally:
        main.forget_snapshot(*key)
