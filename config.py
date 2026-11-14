"""Runtime configuration for the focused Save Post + Clone Channel bot."""
import os

from dotenv import load_dotenv

load_dotenv()

# Telegram and MongoDB connection settings.
API_ID = os.getenv("API_ID", "")
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MONGO_DB = os.getenv("MONGO_DB", "")
DB_NAME = os.getenv("DB_NAME", "telegram_post_vault")

# Operators can refresh the one-item Telegram command list with /set.
OWNER_ID = [int(value) for value in os.getenv("OWNER_ID", "").split() if value]

# Optional required-subscription channel. Set 0 to disable it.
FORCE_SUB = int(os.getenv("FORCE_SUB", "0"))

# Optional user session and log channel used only for the existing large-file
# delivery path. They are not needed for ordinary single-post saving.
STRING = os.getenv("STRING") or None
LOG_GROUP = int(os.getenv("LOG_GROUP", "-1001234456"))

# Credentials saved by the account flow are encrypted at rest. Existing
# encrypted records need the same values until they are deliberately migrated.
MASTER_KEY = os.getenv("MASTER_KEY", "gK8HzLfT9QpViJcYeB5wRa3DmN7P2xUq")
IV_KEY = os.getenv("IV_KEY", "s7Yx5CpVmE3F")
