import time

from app import main


def test_expired_snapshot_removed_even_after_access(monkeypatch):
    monkeypatch.setattr(main, "SNAPSHOT_TTL", 60)

    main.SNAPSHOTS.clear()

    now = time.monotonic()
    stale_key = ("connection", 10, 1)
    fresh_key = ("connection", 10, 2)

    main.SNAPSHOTS[stale_key] = {
        "text": "expired",
        "saved_at": now - 120,
    }
    main.SNAPSHOTS[fresh_key] = {
        "text": "fresh",
        "saved_at": now,
    }

    # Simulate an older snapshot being accessed and moved to the end.
    main.SNAPSHOTS.move_to_end(stale_key)

    try:
        main.prune_memory()

        assert stale_key not in main.SNAPSHOTS
        assert fresh_key in main.SNAPSHOTS
    finally:
        main.SNAPSHOTS.clear()


def test_snapshot_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(main, "MAX_SNAPSHOTS", 2)

    main.SNAPSHOTS.clear()

    try:
        for message_id in range(3):
            main.SNAPSHOTS[("connection", 10, message_id)] = {
                "text": str(message_id),
                "saved_at": time.monotonic(),
            }

        main.prune_memory()

        assert len(main.SNAPSHOTS) == 2
        assert ("connection", 10, 0) not in main.SNAPSHOTS
    finally:
        main.SNAPSHOTS.clear()
