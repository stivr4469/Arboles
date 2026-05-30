import os
import base64
import secrets

# Устанавливаем тестовые переменные ДО любого импорта src.*
# Это решает проблему с Settings() на уровне модуля
os.environ.setdefault("MASTER_ENCRYPTION_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_token")
os.environ.setdefault("TELEGRAM_ADMIN_CHAT_ID", "123456")
