# TR Battle Calculator

A standalone Telegram bot for the TR popularity-battle calculator. It sends a compact HTML Widget in private chats, performs every calculation on the Python server, and stores each user's battle history in Firestore.

## Features

- Individual, home, and team battle modes.
- In-widget numeric keypad and single-screen result view.
- Original scoring tables and formulas preserved (`battle_core.py`).
- Permanent per-user Firestore history with pagination; no automatic record trimming.
- FastAPI Telegram webhook with a secret-token header.
- Render deployment configuration and Firestore indexes included.

## Run

```bash
cp .env.example .env
# Set BOT_TOKEN, WEBHOOK_SECRET and FIREBASE_CREDENTIALS_JSON.
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Set `WEBHOOK_URL` to a public HTTPS endpoint, or deploy on Render and let the app use `RENDER_EXTERNAL_URL`. The app derives a Telegram-compatible webhook secret from `WEBHOOK_SECRET`. Open the bot in a private chat and send `/start` or `/widget`.

See [README_AR.md](README_AR.md) for the full Arabic setup and Firebase instructions.
