from app.main import extract_media


def test_photo_extracts_largest_file():
    message = {
        "photo": [
            {"file_id": "small", "file_unique_id": "s"},
            {"file_id": "large", "file_unique_id": "l"},
        ]
    }
    assert extract_media(message) == ("photo", "large", "l")


def test_video_extracts_file():
    message = {
        "video": {"file_id": "video-id", "file_unique_id": "video-unique"}
    }
    assert extract_media(message) == ("video", "video-id", "video-unique")


def test_no_media():
    assert extract_media({"text": "hello"}) == (None, None, None)
