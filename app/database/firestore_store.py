import asyncio
import json
import os


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
        }

    return records


def _write_connection_sync(owner_id, connection_id, owner_chat_id):
    from firebase_admin import firestore

    db = _get_db()

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
    )
