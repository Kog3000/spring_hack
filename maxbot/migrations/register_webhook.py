"""Регистрирует webhook у MAX Bot API.

Запускайте один раз после деплоя (или при смене публичного URL).

Usage:
    python -m migrations.register_webhook

После запуска MAX начнёт слать события на {PUBLIC_BASE_URL}/webhook/max.
"""
from app.config import Config
from app.logging_setup import get_logger, setup_logging
from app.services.max_client import MaxBotClient


def main():
    setup_logging()
    log = get_logger(__name__)
    Config.validate()

    client = MaxBotClient()

    me = client.get_me()
    log.info("Бот: %s (id=%s)", me.get("username"), me.get("user_id"))

    webhook_url = Config.PUBLIC_BASE_URL.rstrip("/") + "/webhook/max"

    # Удаляем старую подписку, если есть
    try:
        subs = client.get_subscriptions().get("subscriptions") or []
        for sub in subs:
            url = sub.get("url")
            if url:
                log.info("Удаляем старую подписку: %s", url)
                try:
                    client.unsubscribe_webhook(url)
                except Exception as e:
                    log.warning("Не удалось удалить %s: %s", url, e)
    except Exception as e:
        log.warning("Не удалось получить старые подписки: %s", e)

    # Подписываемся на новый URL
    log.info("Регистрируем webhook: %s", webhook_url)
    resp = client.subscribe_webhook(webhook_url, Config.WEBHOOK_SECRET)
    log.info("Ответ MAX: %s", resp)
    log.info("Готово. Бот будет получать события на %s", webhook_url)


if __name__ == "__main__":
    main()
