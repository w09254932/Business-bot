import asyncio

import pytest

from app import main


def test_all_eight_media_types_use_correct_telegram_methods(monkeypatch):
    expected = {
        "photo": ("sendPhoto", "photo"),
        "video": ("sendVideo", "video"),
        "voice": ("sendVoice", "voice"),
        "video_note": ("sendVideoNote", "video_note"),
        "audio": ("sendAudio", "audio"),
        "document": ("sendDocument", "document"),
        "animation": ("sendAnimation", "animation"),
        "sticker": ("sendSticker", "sticker"),
    }

    calls = []

    async def fake_telegram(method, payload):
        calls.append((method, payload))
        return {"message_id": 1}

    monkeypatch.setattr(main, "telegram", fake_telegram)

    async def scenario():
        for media_type in expected:
            await main.send_saved_media(
                12345,
                media_type,
                f"{media_type}-file-id",
            )

    asyncio.run(scenario())

    assert len(calls) == len(expected)

    for media_type, (method, field) in expected.items():
        assert (method, {
            "chat_id": 12345,
            field: f"{media_type}-file-id",
        }) in calls


def test_unsupported_media_type_is_rejected():
    async def scenario():
        with pytest.raises(ValueError):
            await main.send_saved_media(
                12345,
                "unknown",
                "file-id",
            )

    asyncio.run(scenario())


def test_memory_janitor_runs_periodic_cleanup(monkeypatch):
    cleanup_calls = []

    def fake_prune():
        cleanup_calls.append(True)

    async def stop_after_one_cycle(seconds):
        assert seconds == main.MEMORY_CLEANUP_INTERVAL_SECONDS
        raise asyncio.CancelledError

    monkeypatch.setattr(main, "prune_memory", fake_prune)
    monkeypatch.setattr(main.asyncio, "sleep", stop_after_one_cycle)

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await main.memory_janitor()

    asyncio.run(scenario())

    assert cleanup_calls == [True]
    assert main.MEMORY_CLEANUP_INTERVAL_SECONDS == 60
