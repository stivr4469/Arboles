from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://adpilot:changeme@localhost:5432/adpilot"

    # ClickHouse
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 9000
    clickhouse_db: str = "adpilot"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Security
    master_encryption_key: str  # обязательно, base64-encoded 32 bytes

    # Telegram
    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""  # chat_id для утренних отчётов

    # App
    environment: str = "development"
    debug: bool = True
    tenant_timezone: str = "UTC"


settings = Settings()
