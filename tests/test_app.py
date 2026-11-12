import unittest
from unittest.mock import AsyncMock, patch

from app.main import handle_message


class AppCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_in_group_does_not_send_widget(self):
        client = AsyncMock()
        with patch("app.main.telegram_api", new_callable=AsyncMock) as api:
            await handle_message({"chat": {"id": -1001, "type": "supergroup"}, "text": "/start"}, client)
        api.assert_awaited_once()
        self.assertEqual(api.await_args.args[1], "sendMessage")

    async def test_help_sends_help_message(self):
        client = AsyncMock()
        with patch("app.main.telegram_api", new_callable=AsyncMock) as api:
            await handle_message({"chat": {"id": 123, "type": "private"}, "text": "/help"}, client)
        api.assert_awaited_once()
        self.assertEqual(api.await_args.args[1], "sendMessage")


if __name__ == "__main__":
    unittest.main()
