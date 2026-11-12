# -*- coding: utf-8 -*-
"""Patch the existing repository's main.py to enable /widget and raw widget_query handling.

Run from the repository root: python install_widget.py
A timestamp-free main.py.bak is created before the first edit.
"""
from pathlib import Path
import re
import shutil
import sys

MAIN = Path("main.py")
MARKER = "# TR_BATTLE_WIDGET_INTEGRATION_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Cannot safely patch {label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    if not MAIN.is_file():
        print("ERROR: run this script from the repository root (main.py not found).")
        return 2
    source = MAIN.read_text(encoding="utf-8")
    if MARKER in source:
        print("Widget integration is already installed in main.py.")
        return 0

    patched = source
    patched = replace_once(
        patched,
        "import richbot\n",
        "import richbot\nimport widget_calculator\n",
        "widget module import",
    )
    patched = replace_once(
        patched,
        "    PreCheckoutQuery,\n",
        "    PreCheckoutQuery,\n    Update,\n",
        "aiogram Update import",
    )
    patched = replace_once(
        patched,
        '"pre_checkout_query"],',
        '"pre_checkout_query", "widget_query"],',
        "allowed_updates",
    )
    patched = replace_once(
        patched,
        "    trim_history(user.id, mode, keep=3)\n",
        "    # Widget history mode keeps every calculation; no automatic trimming.\n",
        "permanent battle history",
    )
    patched = replace_once(
        patched,
        'await message.answer("👋 أرسل /start لاختيار نوع المعركة.")\n\n# ---------------- Webhook app ----------------',
        'await message.answer("👋 أرسل /start لاختيار نوع المعركة.")\n\n'
        '@dp.message(Command("widget"), F.chat.type == "private")\n'
        'async def widget_calculator_cmd(message: Message):\n'
        '    ok, detail = await widget_calculator.send_widget(BOT_TOKEN, message.chat.id)\n'
        '    if not ok:\n'
        '        await message.answer(f"⚠️ تعذّر إرسال الحاسبة: {detail}")\n\n'
        '# ---------------- Webhook app ----------------',
        "/widget command",
    )

    raw_handler = f'''{MARKER}\nasync def telegram_webhook_raw(request):\n    """Handle new Widget updates before passing legacy updates to Aiogram."""\n    try:\n        payload = await request.json()\n    except Exception:\n        return web.Response(status=400, text="bad json")\n\n    try:\n        if isinstance(payload, dict) and "widget_query" in payload:\n            await widget_calculator.handle_widget_query(\n                BOT_TOKEN, payload["widget_query"], db, compute_battle, register_user=register_user\n            )\n            return web.Response(text="ok")\n\n        update = Update.model_validate(payload, context={{"bot": bot}})\n        await dp.feed_update(bot, update)\n        return web.Response(text="ok")\n    except Exception:\n        logger.exception("raw Telegram webhook handler failed")\n        # Return 200 to avoid retry storms for poison updates; inspect Render logs.\n        return web.Response(text="handled with error")\n\n\n'''
    patched = replace_once(
        patched,
        "def main():\n",
        raw_handler + "def main():\n",
        "raw widget webhook handler",
    )
    patched = replace_once(
        patched,
        "SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)",
        "app.router.add_post(WEBHOOK_PATH, telegram_webhook_raw)",
        "raw webhook route",
    )

    backup = Path("main.py.bak")
    if not backup.exists():
        shutil.copy2(MAIN, backup)
    MAIN.write_text(patched, encoding="utf-8")
    print("Installed Telegram Widget integration.")
    print("- Added /widget command")
    print("- Added widget_query to allowed_updates")
    print("- Routed raw widget_query updates and forwarded all legacy updates to Aiogram")
    print("- Preserved the original compute_battle() as the authoritative calculator")
    print("Backup: main.py.bak")
    print("Next: deploy firestore.indexes.json, then deploy the service.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
