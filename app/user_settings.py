"""Validated per-owner media and retention settings."""

DEFAULT_USER_SETTINGS = {
    "capture_once": True,
    "capture_any_media": False,
    "rescue_photo": True,
    "rescue_video": True,
    "rescue_voice": True,
    "rescue_audio": True,
    "rescue_document": True,
    "rescue_animation": True,
    "rescue_video_note": True,
    "rescue_sticker": True,
    "retention_hours": 6,
}

BOOLEAN_SETTINGS = frozenset(
    key for key, value in DEFAULT_USER_SETTINGS.items()
    if isinstance(value, bool)
)

RETENTION_OPTIONS = (1, 6, 12, 24, 72, 168)


def normalize_user_settings(data=None):
    result = DEFAULT_USER_SETTINGS.copy()

    if not isinstance(data, dict):
        return result

    for key in BOOLEAN_SETTINGS:
        value = data.get(key)
        if isinstance(value, bool):
            result[key] = value

    try:
        hours = int(data.get("retention_hours", result["retention_hours"]))
    except (TypeError, ValueError):
        hours = result["retention_hours"]

    if hours in RETENTION_OPTIONS:
        result["retention_hours"] = hours

    return result
