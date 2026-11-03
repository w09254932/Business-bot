import asyncio
import json

from app.media import cache


def test_cache_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CACHE_MEDIA", raising=False)
    assert cache.cache_enabled() is False


def test_media_metadata_extracts_largest_photo():
    result = cache.media_metadata({
        "photo": [
            {"file_id": "small"},
            {"file_id": "large", "file_unique_id": "unique"},
        ]
    })

    assert result["media_type"] == "photo"
    assert result["file_id"] == "large"
    assert result["file_unique_id"] == "unique"


def test_media_metadata_supports_video():
    result = cache.media_metadata({
        "video": {"file_id": "video-id", "file_size": 100}
    })

    assert result["media_type"] == "video"
    assert result["file_size"] == 100


def test_declared_oversized_file_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("CACHE_MEDIA", "true")
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_CACHE_FILE_MB", "1")

    result = asyncio.run(
        cache.cache_telegram_media(
            "fake-token",
            "file-id",
            declared_size=2 * 1024 * 1024,
        )
    )

    assert result is None


def test_cache_downloads_file_with_mocked_telegram(monkeypatch, tmp_path):
    monkeypatch.setenv("CACHE_MEDIA", "true")
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_CACHE_FILE_MB", "1")
    monkeypatch.setenv("MAX_CACHE_TOTAL_MB", "2")

    class Response:
        def __init__(self, status, payload=None, content=b""):
            self.status_code = status
            self._payload = payload
            self.content = content

        def json(self):
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None):
            return Response(
                200,
                {"ok": True, "result": {
                    "file_path": "photos/test.jpg",
                    "file_size": 5,
                }},
            )

        async def get(self, url):
            return Response(200, content=b"image")

    monkeypatch.setattr(
        cache.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    result = asyncio.run(
        cache.cache_telegram_media(
            "fake-token",
            "photo-file-id",
            "photo-unique-id",
        )
    )

    assert result is not None
    assert (tmp_path / result.split("/")[-1]).read_bytes() == b"image"


def test_cache_deletes_only_inside_cache_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))

    inside = tmp_path / "cached.jpg"
    inside.write_bytes(b"data")

    outside = tmp_path.parent / "outside.jpg"
    outside.write_bytes(b"data")

    try:
        assert cache.delete_cached_media(str(inside)) is True
        assert not inside.exists()

        assert cache.delete_cached_media(str(outside)) is False
        assert outside.exists()
    finally:
        outside.unlink(missing_ok=True)


def test_cache_rejects_unknown_media_type():
    async def scenario():
        try:
            await cache.upload_cached_media(
                "fake-token",
                123,
                "unknown",
                "missing.bin",
            )
        except ValueError:
            return
        raise AssertionError("Unknown media type was accepted")

    asyncio.run(scenario())
