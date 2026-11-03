import asyncio

import httpx

from app import main


def test_webhook_rejects_invalid_secret(monkeypatch):
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "test-secret")

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/telegram/webhook",
                headers={
                    "X-Telegram-Bot-Api-Secret-Token": "wrong-secret"
                },
                json={"update_id": 987654321},
            )

        assert response.status_code == 403

    asyncio.run(scenario())


def test_webhook_processes_update_only_once(monkeypatch):
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "test-secret")

    update_id = 987654322
    main.PROCESSED_UPDATES.pop(update_id, None)
    calls = []

    async def fake_handle_update(update):
        calls.append(update["update_id"])

    monkeypatch.setattr(main, "handle_update", fake_handle_update)

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            headers = {
                "X-Telegram-Bot-Api-Secret-Token": "test-secret"
            }
            payload = {"update_id": update_id}

            first = await client.post(
                "/telegram/webhook",
                headers=headers,
                json=payload,
            )
            second = await client.post(
                "/telegram/webhook",
                headers=headers,
                json=payload,
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["processed"] is True
        assert second.json()["duplicate"] is True
        assert calls == [update_id]

    try:
        asyncio.run(scenario())
    finally:
        main.PROCESSED_UPDATES.pop(update_id, None)
