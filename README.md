# Business Bot — Channel-backed message change alerts

A minimal Telegram Business bot that archives incoming and outgoing **text messages and captions** into one private Telegram channel. It intentionally does not notify the connected account for new messages. When a message is edited or deleted, it retrieves the last archived text from the channel using MTProto, publishes an audit event to the channel, and notifies the connected business account.

## Architecture

- **Bot API / FastAPI webhook:** receives `business_connection`, `business_message`, `edited_business_message`, and `deleted_business_messages`; sends archive posts and owner alerts.
- **MTProto / Telethon using the same bot token:** calls `channels.getMessages` to fetch known archive message IDs. Telegram documents this method as usable by both users and bots.
- **SQLite index:** stores identifiers and routing metadata only: business connection ID, owner/chat/message IDs, archive message IDs, direction, and deletion status. It does not store message text.
- **Private archive channel:** the actual text/caption snapshots and change history live here.

## Important first-version scope

- Archives text and media captions. It does **not** copy photo/video/document/audio binaries in this first version.
- Does not notify on new messages.
- Notifies the connected account when an archived message is edited or deleted, whether incoming or outgoing.
- Every edit creates a new immutable audit record in the archive channel. It does not overwrite prior history.
- If the original message has no index entry, the bot reports that the previous version could not be recovered; it does not invent old content.

## Telegram setup

1. Create a bot with @BotFather and enable Telegram Business / Secretary Mode.
2. Create a **private** archive channel.
3. Add the bot as an administrator of the archive channel with permission to post messages. The bot must be a member and able to access the messages it posts.
4. Get `API_ID` and `API_HASH` at https://my.telegram.org/apps. These are required because the Bot API cannot fetch a channel post by message ID; Telethon logs in as the bot via MTProto using the same bot token.
5. Copy `.env.example` to `.env` and fill all required values.
6. The business account owner connects the bot and grants access to the chats they choose. Telegram must send `business_connection` updates to this bot.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env, including WEBHOOK_URL if using webhook registration
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Telegram webhooks require a public HTTPS URL. For a development environment, expose port 8000 through a trusted HTTPS tunnel or Codespaces forwarded port, then set `WEBHOOK_URL` to `https://YOUR_PUBLIC_HOST/telegram/webhook`.

## Render deployment

Use a Python Web Service:

- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check: `/health`
- Set all `.env` variables in Render's Environment settings.
- Attach a persistent disk and set `DB_PATH=/var/data/index.sqlite3` and `SESSION_PATH=/var/data/archive_bot` so the identifier index and Telethon bot session survive restarts.
- Set `WEBHOOK_URL=https://YOUR-SERVICE.onrender.com/telegram/webhook`.

## Security and privacy

The archive channel contains customer message text. Keep it private, restrict administrators, disclose the archive to affected users where required, and define a retention policy. Never commit `.env`, bot tokens, API hashes, or Telethon session files. A Telethon session file is an authentication credential and must be protected like the bot token.

## Operational caveats

- Telegram Bot API updates are retained for no more than 24 hours if not collected.
- There can be a short gap or partial archive if Telegram delivery or the app fails mid-processing; monitor logs and consider a durable job queue for production.
- SQLite is suitable for a single-instance MVP. Use PostgreSQL for multi-instance/high-volume deployments.
- Before production, add automated tests, rate-limit handling, retry/outbox semantics, and media-file archiving if required.
