from app.store import Store


def test_store_keeps_only_message_index(tmp_path):
    store = Store(str(tmp_path / "index.sqlite3"))
    store.initialize()
    store.save_connection("conn", 10, 11, True)
    store.save_message(
        connection_id="conn", chat_id=20, source_message_id=30,
        owner_id=10, owner_chat_id=11, direction="incoming",
        sender="Customer", original_header_id=40,
        latest_snapshot_ids=[41, 42], deleted=False,
    )
    found = store.get_message("conn", 20, 30)
    assert found["latest_snapshot_ids"] == [41, 42]
    assert "text" not in found
