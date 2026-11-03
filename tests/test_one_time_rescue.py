import asyncio

from app.media.one_time_rescue import (
    extract_reply_media,
    rescue_reply_media,
)


def test_extract_photo():
    message = {
        "photo": [
            {"file_id": "small"},
            {"file_id": "large", "file_unique_id": "unique"},
        ]
    }

    assert extract_reply_media(message) == {
        "media_type": "photo",
        "file_id": "large",
        "file_unique_id": "unique",
    }


def test_extract_video():
    message = {
        "video": {
            "file_id": "video-id",
            "file_unique_id": "video-unique",
        }
    }

    assert extract_reply_media(message)["media_type"] == "video"


def test_missing_media():
    assert extract_reply_media({"text": "hello"}) is None
    assert extract_reply_media(None) is None


def test_media_without_file_id():
    assert extract_reply_media({"video": {"duration": 5}}) is None


def test_rescue_sends_video():
    async def scenario():
        calls = []

        async def sender(chat_id, media_type, file_id):
            calls.append((chat_id, media_type, file_id))

        result = await rescue_reply_media(
            {
                "reply_to_message": {
                    "video": {"file_id": "video-123"}
                }
            },
            12345,
            sender,
        )

        assert result["ok"] is True
        assert calls == [(12345, "video", "video-123")]

    asyncio.run(scenario())


def test_rescue_reports_unavailable_media():
    async def scenario():
        async def sender(*args):
            raise AssertionError("Sender must not be called")

        result = await rescue_reply_media(
            {"reply_to_message": {"text": "no media"}},
            12345,
            sender,
        )

        assert result == {
            "ok": False,
            "reason": "media_unavailable",
        }

    asyncio.run(scenario())


def test_rescue_handles_send_failure():
    async def scenario():
        async def sender(*args):
            raise RuntimeError("send failed")

        result = await rescue_reply_media(
            {
                "reply_to_message": {
                    "photo": [{"file_id": "photo-123"}]
                }
            },
            12345,
            sender,
        )

        assert result["ok"] is False
        assert result["reason"] == "telegram_send_failed"

    asyncio.run(scenario())
