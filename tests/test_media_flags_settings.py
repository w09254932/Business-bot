import asyncio

from app.media.one_time_rescue import (
    extract_reply_media,
    is_one_time,
    rescue_reply_media,
)
from app.user_settings import (
    DEFAULT_USER_SETTINGS,
    normalize_user_settings,
)


def test_detect_view_once_flag():
    assert is_one_time({
        "photo": [{"file_id": "photo-id"}],
        "view_once": True,
    })


def test_detect_ttl_on_media_object():
    assert is_one_time({
        "video": {
            "file_id": "video-id",
            "ttl_seconds": 10,
        }
    })


def test_zero_ttl_is_not_one_time():
    assert not is_one_time({
        "voice": {
            "file_id": "voice-id",
            "ttl_seconds": 0,
        }
    })


def test_no_marker_is_not_assumed_one_time():
    assert not is_one_time({
        "photo": [{"file_id": "photo-id"}],
    })


def test_extract_all_supported_media():
    for media_type in (
        "photo", "video", "voice", "video_note",
        "audio", "document", "animation", "sticker",
    ):
        item = {"file_id": f"{media_type}-id"}
        message = (
            {"photo": [item]}
            if media_type == "photo"
            else {media_type: item}
        )
        assert extract_reply_media(message)["media_type"] == media_type


def test_settings_reject_invalid_values():
    settings = normalize_user_settings({
        "capture_any_media": "yes",
        "rescue_photo": False,
        "retention_hours": 999,
        "unknown_setting": True,
    })

    assert settings["capture_any_media"] is False
    assert settings["rescue_photo"] is False
    assert settings["retention_hours"] == 6
    assert "unknown_setting" not in settings


def test_capture_any_media_allows_normal_media():
    async def scenario():
        calls = []

        async def sender(*args):
            calls.append(args)

        result = await rescue_reply_media(
            {
                "reply_to_message": {
                    "photo": [{"file_id": "photo-id"}],
                }
            },
            100,
            sender,
            {
                **DEFAULT_USER_SETTINGS,
                "capture_any_media": True,
            },
        )

        assert result["ok"] is True
        assert calls == [(100, "photo", "photo-id")]

    asyncio.run(scenario())


def test_normal_media_is_not_rescued_by_default():
    async def scenario():
        async def sender(*args):
            raise AssertionError("Should not send")

        result = await rescue_reply_media(
            {
                "reply_to_message": {
                    "video": {"file_id": "video-id"},
                }
            },
            100,
            sender,
            DEFAULT_USER_SETTINGS,
        )

        assert result["reason"] == "not_one_time"

    asyncio.run(scenario())


def test_disabled_media_type_is_not_rescued():
    async def scenario():
        async def sender(*args):
            raise AssertionError("Should not send")

        settings = {
            **DEFAULT_USER_SETTINGS,
            "rescue_photo": False,
            "capture_any_media": True,
        }

        result = await rescue_reply_media(
            {
                "reply_to_message": {
                    "photo": [{"file_id": "photo-id"}],
                }
            },
            100,
            sender,
            settings,
        )

        assert result["reason"] == "media_type_disabled"

    asyncio.run(scenario())
