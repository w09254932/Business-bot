import asyncio
import json
import os
import time


ENV_KEY = "FIREBASE_SERVICE_ACCOUNT_JSON"
COLLECTION_NAME = "business_connections"


def is_configured() -> bool:
    return bool(os.getenv(ENV_KEY, "").strip())


def storage_mode() -> str:
    return "Firestore + RAM" if is_configured() else "RAM only"


def _get_db():
    raw = os.getenv(ENV_KEY, "").strip()

    if not raw:
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        service_account = json.loads(raw)

        if not isinstance(service_account, dict):
            raise ValueError("Invalid service account format")

        try:
            app = firebase_admin.get_app()
        except ValueError:
            credential = credentials.Certificate(service_account)
            app = firebase_admin.initialize_app(credential)

        return firestore.client(app=app)

    except Exception:
        raise RuntimeError("Firestore initialization failed") from None


def _load_connections_sync():
    db = _get_db()
    records = {}

    documents = db.collection_group(COLLECTION_NAME).stream()

    for document in documents:
        data = document.to_dict() or {}

        if data.get("enabled") is not True:
            continue

        try:
            owner_id = int(data["owner_id"])
            owner_chat_id = int(data["owner_chat_id"])
        except (KeyError, TypeError, ValueError):
            continue

        records[document.id] = {
            "owner_id": owner_id,
            "owner_chat_id": owner_chat_id,
            "enabled": True,
            "rights": data.get("rights") or {},
            "business_user": data.get("business_user") or {},
            "connected_at": data.get("connected_at"),
        }

    return records

def _write_connection_sync(
    owner_id,
    connection_id,
    owner_chat_id,
    connection_data=None,
):
    from firebase_admin import firestore

    db = _get_db()
    connection_data = connection_data or {}
    user = connection_data.get("user") or {}
    raw_rights = connection_data.get("rights") or {}

    safe_rights = {
        key: value
        for key, value in raw_rights.items()
        if isinstance(value, bool)
    }

    business_user = {
        key: user[key]
        for key in ("id", "first_name", "last_name", "username")
        if key in user
    }

    reference = (
        db.collection("users")
        .document(str(owner_id))
        .collection(COLLECTION_NAME)
        .document(connection_id)
    )

    reference.set({
        "owner_id": int(owner_id),
        "owner_chat_id": int(owner_chat_id),
        "enabled": True,
        "rights": safe_rights,
        "business_user": business_user,
        "connected_at": int(connection_data.get("date") or time.time()),
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)

def _delete_connection_sync(owner_id, connection_id):
    db = _get_db()

    reference = (
        db.collection("users")
        .document(str(owner_id))
        .collection(COLLECTION_NAME)
        .document(connection_id)
    )

    # Firestore does not automatically delete subcollections.
    messages = reference.collection("messages")

    while True:
        documents = list(messages.limit(400).stream())

        if not documents:
            break

        batch = db.batch()

        for document in documents:
            batch.delete(document.reference)

        batch.commit()

    # Keep message_archive as durable audit metadata after disconnection.
    reference.delete()


async def load_connections():
    if not is_configured():
        return None

    return await asyncio.to_thread(_load_connections_sync)


async def persist_business_connection(data: dict):
    if not is_configured():
        return

    connection_id = data.get("id")
    user = data.get("user") or {}
    owner_id = user.get("id")
    owner_chat_id = data.get("user_chat_id")

    if not connection_id or not owner_id or not owner_chat_id:
        raise ValueError("Incomplete business connection data")

    if not data.get("is_enabled"):
        await asyncio.to_thread(
            _delete_connection_sync,
            owner_id,
            connection_id,
        )
        return

    await asyncio.to_thread(
        _write_connection_sync,
        owner_id,
        connection_id,
        owner_chat_id,
        data,
    )

def _load_user_settings_sync(owner_ids):
    db = _get_db()
    result = {}

    for owner_id in owner_ids:
        reference = (
            db.collection("users")
            .document(str(owner_id))
            .collection("settings")
            .document("media")
        )
        document = reference.get()

        if document.exists:
            result[str(owner_id)] = document.to_dict() or {}

    return result


def _write_user_settings_sync(owner_id, settings):
    from firebase_admin import firestore

    db = _get_db()
    reference = (
        db.collection("users")
        .document(str(owner_id))
        .collection("settings")
        .document("media")
    )

    reference.set({
        "owner_id": int(owner_id),
        **settings,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)


async def load_user_settings(owner_ids):
    if not is_configured():
        return None

    unique_ids = sorted({int(owner_id) for owner_id in owner_ids})
    if not unique_ids:
        return {}

    return await asyncio.to_thread(
        _load_user_settings_sync,
        unique_ids,
    )


async def persist_user_settings(owner_id, settings):
    if not is_configured():
        return

    from app.user_settings import normalize_user_settings

    safe_settings = normalize_user_settings(settings)

    await asyncio.to_thread(
        _write_user_settings_sync,
        int(owner_id),
        safe_settings,
    )



# ---------------------------------------------------------------------------
# Persistent message snapshots
# Path:
# users/{owner_id}/business_connections/{connection_id}/messages/{chat}_{msg}
# ---------------------------------------------------------------------------

def _message_reference(
    db,
    owner_id,
    connection_id,
    chat_id,
    message_id,
):
    document_id = f"{int(chat_id)}_{int(message_id)}"

    return (
        db.collection("users")
        .document(str(int(owner_id)))
        .collection(COLLECTION_NAME)
        .document(str(connection_id))
        .collection("messages")
        .document(document_id)
    )


def _write_message_snapshot_sync(
    owner_id,
    connection_id,
    snapshot,
):
    from firebase_admin import firestore

    db = _get_db()

    reference = _message_reference(
        db,
        owner_id,
        connection_id,
        snapshot["chat_id"],
        snapshot["message_id"],
    )

    data = {
        "owner_id": int(owner_id),
        "connection_id": str(connection_id),
        "chat_id": int(snapshot["chat_id"]),
        "message_id": int(snapshot["message_id"]),
        "sender_id": snapshot.get("sender_id"),
        "text": str(snapshot.get("text") or "")[:4096],
        "media_type": snapshot.get("media_type"),
        "file_id": snapshot.get("file_id"),
        "file_unique_id": snapshot.get("file_unique_id"),
        "saved_at_unix": int(
            snapshot.get("saved_at_unix") or time.time()
        ),
        "updated_at": firestore.SERVER_TIMESTAMP,
    }

    reference.set(data, merge=True)


def _read_message_snapshot_sync(
    owner_id,
    connection_id,
    chat_id,
    message_id,
):
    db = _get_db()

    reference = _message_reference(
        db,
        owner_id,
        connection_id,
        chat_id,
        message_id,
    )

    document = reference.get()

    if not document.exists:
        return None

    data = document.to_dict() or {}
    data["file_path"] = None

    return data


def _pop_message_snapshot_sync(
    owner_id,
    connection_id,
    chat_id,
    message_id,
):
    db = _get_db()

    reference = _message_reference(
        db,
        owner_id,
        connection_id,
        chat_id,
        message_id,
    )

    document = reference.get()

    if not document.exists:
        return None

    data = document.to_dict() or {}
    reference.delete()
    data["file_path"] = None

    return data


def _delete_message_snapshot_sync(
    owner_id,
    connection_id,
    chat_id,
    message_id,
):
    db = _get_db()

    reference = _message_reference(
        db,
        owner_id,
        connection_id,
        chat_id,
        message_id,
    )

    reference.delete()


def _prune_message_snapshots_sync(
    owner_id,
    connection_id,
    retention_hours,
):
    db = _get_db()

    cutoff = int(time.time()) - int(retention_hours) * 3600

    collection = (
        db.collection("users")
        .document(str(int(owner_id)))
        .collection(COLLECTION_NAME)
        .document(str(connection_id))
        .collection("messages")
    )

    removed = 0

    # Bounded batches keep each cleanup cycle manageable.
    for _ in range(10):
        documents = list(
            collection
            .where("saved_at_unix", "<", cutoff)
            .limit(400)
            .stream()
        )

        if not documents:
            break

        batch = db.batch()

        for document in documents:
            batch.delete(document.reference)

        batch.commit()
        removed += len(documents)

        if len(documents) < 400:
            break

    return removed


async def persist_message_snapshot(
    owner_id,
    connection_id,
    snapshot,
):
    if not is_configured():
        return False

    required = ("chat_id", "message_id")

    if any(snapshot.get(key) is None for key in required):
        raise ValueError("Incomplete message snapshot")

    await asyncio.to_thread(
        _write_message_snapshot_sync,
        owner_id,
        connection_id,
        snapshot,
    )

    return True


async def load_message_snapshot(
    owner_id,
    connection_id,
    chat_id,
    message_id,
):
    if not is_configured():
        return None

    return await asyncio.to_thread(
        _read_message_snapshot_sync,
        owner_id,
        connection_id,
        chat_id,
        message_id,
    )


async def pop_message_snapshot(
    owner_id,
    connection_id,
    chat_id,
    message_id,
):
    if not is_configured():
        return None

    return await asyncio.to_thread(
        _pop_message_snapshot_sync,
        owner_id,
        connection_id,
        chat_id,
        message_id,
    )


async def delete_message_snapshot(
    owner_id,
    connection_id,
    chat_id,
    message_id,
):
    if not is_configured():
        return

    await asyncio.to_thread(
        _delete_message_snapshot_sync,
        owner_id,
        connection_id,
        chat_id,
        message_id,
    )


async def prune_message_snapshots(
    owner_id,
    connection_id,
    retention_hours,
):
    if not is_configured():
        return 0

    return await asyncio.to_thread(
        _prune_message_snapshots_sync,
        owner_id,
        connection_id,
        retention_hours,
    )

def _archive_reference(db, owner_id, connection_id, chat_id, message_id):
    document_id = f"{int(chat_id)}_{int(message_id)}"
    return (
        db.collection("users")
        .document(str(int(owner_id)))
        .collection(COLLECTION_NAME)
        .document(str(connection_id))
        .collection("message_archive")
        .document(document_id)
    )


def _write_archive_record_sync(owner_id, connection_id, record):
    from firebase_admin import firestore

    db = _get_db()
    reference = _archive_reference(
        db, owner_id, connection_id,
        record["chat_id"], record["message_id"],
    )

    previous_document = reference.get()
    previous = previous_document.to_dict() or {}

    archive_message_id = int(record["archive_message_id"])
    data = {
        "owner_id": int(owner_id),
        "connection_id": str(connection_id),
        "chat_id": int(record["chat_id"]),
        "message_id": int(record["message_id"]),
        "archive_chat_id": record["archive_chat_id"],
        "first_archive_message_id": previous.get(
            "first_archive_message_id", archive_message_id
        ),
        "latest_archive_message_id": archive_message_id,
        "archive_media_message_id": record.get(
            "archive_media_message_id"
        ),
        "media_status": record.get("media_status", "not_applicable"),
        "media_error": str(record.get("media_error") or "")[:500],
        "direction": record.get("direction"),
        "event": record.get("event", "new"),
        "is_deleted": previous.get("is_deleted", False),
        "updated_at": firestore.SERVER_TIMESTAMP,
    }

    if not previous_document.exists:
        data["created_at"] = firestore.SERVER_TIMESTAMP

    reference.set(data, merge=True)


def _read_archive_record_sync(
    owner_id, connection_id, chat_id, message_id
):
    db = _get_db()
    reference = _archive_reference(
        db, owner_id, connection_id, chat_id, message_id
    )
    document = reference.get()

    if not document.exists:
        return None

    return document.to_dict() or {}


def _mark_archive_deleted_sync(
    owner_id, connection_id, chat_id, message_id, notice_message_id=None
):
    from firebase_admin import firestore

    db = _get_db()
    reference = _archive_reference(
        db, owner_id, connection_id, chat_id, message_id
    )
    document = reference.get()

    if not document.exists:
        return False

    data = {
        "is_deleted": True,
        "deleted_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    if notice_message_id is not None:
        data["delete_notice_message_id"] = int(notice_message_id)

    reference.set(data, merge=True)
    return True


async def persist_archive_record(owner_id, connection_id, record):
    if not is_configured():
        return False

    required = (
        "chat_id", "message_id", "archive_chat_id", "archive_message_id"
    )
    if any(record.get(key) is None for key in required):
        raise ValueError("Incomplete archive record")

    await asyncio.to_thread(
        _write_archive_record_sync, owner_id, connection_id, record
    )
    return True


async def load_archive_record(
    owner_id, connection_id, chat_id, message_id
):
    if not is_configured():
        return None

    return await asyncio.to_thread(
        _read_archive_record_sync,
        owner_id, connection_id, chat_id, message_id,
    )


async def mark_archive_deleted(
    owner_id, connection_id, chat_id, message_id,
    notice_message_id=None,
):
    if not is_configured():
        return False

    return await asyncio.to_thread(
        _mark_archive_deleted_sync,
        owner_id, connection_id, chat_id, message_id, notice_message_id,
    )
