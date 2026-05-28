"""Конфигурация приложения: загружает переменные окружения из .env."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Все настройки приложения берутся из переменных окружения."""

    # MAX Bot API
    MAX_BOT_TOKEN: str = os.getenv("MAX_BOT_TOKEN", "")
    MAX_API_BASE: str = os.getenv("MAX_API_BASE", "https://platform-api.max.ru")

    # Сервер
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "")
    PORT: int = int(os.getenv("PORT", "8080"))

    # Секреты
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "dev-webhook-secret")
    QR_SECRET: str = os.getenv("QR_SECRET", "dev-qr-secret")
    FLASK_SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-flask-secret")

    # БД
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql://maxbot:maxbot@localhost:5432/maxbot"
    )

    # Логирование
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Версия документа согласия. Меняется при изменении правил обработки данных.
    CONSENT_VERSION: str = "v1.0"

    @classmethod
    def validate(cls) -> None:
        """Проверяет, что заданы критичные переменные. Вызывается на старте."""
        missing = []
        if not cls.MAX_BOT_TOKEN or cls.MAX_BOT_TOKEN.startswith("YOUR_"):
            missing.append("MAX_BOT_TOKEN")
        if not cls.PUBLIC_BASE_URL or "example.com" in cls.PUBLIC_BASE_URL:
            missing.append("PUBLIC_BASE_URL")
        if missing:
            raise RuntimeError(
                f"Не заданы переменные окружения: {', '.join(missing)}. "
                "Проверьте файл .env"
            )
