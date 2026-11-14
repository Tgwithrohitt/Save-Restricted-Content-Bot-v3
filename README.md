# 🪞 Post Vault — Save Post & Clone Channel

A deliberately focused Telegram bot for two jobs only:

1. **Save Post** — copy one Telegram post you are allowed to access.
2. **Clone Channel** — mirror new posts from one accessible source channel to your chosen destination while the bot is online.

The `/start` dashboard has exactly three primary destinations:

- **📥 Save Post**
- **⚙️ Settings**
- **👤 Account**

Downloader, premium, payments, gift cards, batch saving, topic batch, task history, reminders, help and owner panels are not loaded by the runtime.

> Use this project only with content and channels you own or are permitted to copy. Telegram permissions, source availability and platform limits still apply.

## Dashboard flow

### 1. Account

Open **Account** and:

- connect the Telegram account that can read the source when required;
- add a custom bot token for sending saved posts; and
- use Account Status to confirm that credentials are stored.

Phone numbers, login codes, passwords and bot tokens are sensitive. The bot tries to delete custom-bot token messages after reading them, but deletion is best effort. Use a deployment you trust and revoke exposed credentials in Telegram Devices or BotFather.

### 2. Settings

Configure the destination before starting a clone:

- **Destination:** a numeric chat ID such as `-1001234567890`; optionally append `/topic-id` for a forum topic.
- **Caption:** text appended to saved media.
- **Media filter:** choose all, photo, video, document, audio or text.
- **Original caption:** keep or remove the source media caption.
- **Language:** English or Hindi dashboard labels.

When no destination is set, an individual saved post goes to the user's private chat. A live channel clone requires an explicit destination. The source and destination cannot be the same chat.

### 3. Save Post

Tap **Save a Post**, then send one Telegram message link. The custom bot delivers the result directly when possible. Private sources require a connected Telegram account with access to that source.

Only single-post saving is supported. Legacy `/batch` does not start a job and directs users back to the dashboard.

### 4. Clone Channel

Tap **Clone Channel** and send the source `@username` or numeric chat ID. The clone is opt-in, one source per user, and starts after the bot validates the source, connected account, custom bot and destination.

Important clone behavior:

- It handles **new posts only** while this worker is online.
- It does **not** backfill historical or missed posts.
- It has one active clone per user.
- Use **Clone Status** and **Stop Clone** from Save Post to manage it.
- A delivery already in progress may finish before a stop takes effect.

## Setup

1. Copy `.env.example` to `.env`.
2. Set `API_ID`, `API_HASH`, `BOT_TOKEN`, `MONGO_DB`, `MASTER_KEY` and `IV_KEY`.
3. Generate unique encryption keys before production. Keep prior values while migrating existing encrypted account records.
4. Install requirements:

   ```bash
   pip install -r requirements.txt
   ```

5. Start the bot worker:

   ```bash
   python main.py
   ```

`/start` opens the dashboard. The command picker is intentionally limited to `/start`; an optional owner can run `/set` to refresh that list.

## Deployment notes

- Run **one bot worker** against a MongoDB database. Multiple workers can duplicate live-clone deliveries.
- `FORCE_SUB=0` disables the optional required-channel check.
- The optional `STRING` and `LOG_GROUP` settings are only for the existing large-file upload path; normal single-post delivery does not require them.
- The bundled web app exposes `/health` for web-process liveness only. It does not prove Telegram or MongoDB readiness.

## Migrating from the previous feature set

Legacy premium/payment/downloader modules remain in the repository history for compatibility and auditability, but `main.py` no longer loads them. Their inline buttons are rejected and returned to the updated dashboard. Legacy running bulk jobs are marked `retired` at startup and are never resumed or delivered.

## Testing

Run the offline test suite with:

```bash
python -m unittest discover -s tests -v
```

The suite uses mocks; it does not replace live Telegram, MongoDB or permission testing.

## License

GPL-3.0 — see [LICENSE](LICENSE). Original copyright notices and third-party attribution remain in place.
