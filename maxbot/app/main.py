"""Точка входа Flask-приложения.

Один процесс Flask раздаёт три точки входа:
  POST /webhook/max  — webhook от MAX Bot API
  GET  /admin/...    — веб-приложение для организатора и админа
  GET  /healthz      — health-check
"""
from __future__ import annotations

import hashlib
import hmac
import uuid

from flask import Flask, abort, g, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .db import session_scope
from .handlers.bot import dispatch
from .logging_setup import get_logger, setup_logging
from .services.max_client import MaxBotClient
from .web.admin import admin_bp


def create_app() -> Flask:
    setup_logging()
    log = get_logger(__name__)

    Config.validate()  # упадёт, если не заданы критичные переменные

    app = Flask(__name__)
    app.config["SECRET_KEY"] = Config.FLASK_SECRET_KEY
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # При деплое на Railway/любой прокси — нужно доверять заголовкам X-Forwarded-*
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    app.register_blueprint(admin_bp)

    @app.before_request
    def _add_request_id():
        g.req_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:8]

    @app.after_request
    def _log_request(resp):
        # Логируем без тела
        log.info(
            "HTTP %s %s → %s",
            request.method,
            request.path,
            resp.status_code,
            extra={"req_id": g.get("req_id", "-")},
        )
        return resp

    @app.route("/")
    def index():
        return jsonify(
            {
                "service": "max-bot-registration",
                "team": "Лимон",
                "docs": "https://dev.max.ru/docs-api",
            }
        )

    @app.route("/healthz")
    def healthz():
        # Простой health-check: пробуем дотянуться до БД
        try:
            from sqlalchemy import text
            with session_scope() as s:
                s.execute(text("SELECT 1"))
            return jsonify({"status": "ok"})
        except Exception as e:
            log.error("healthz: %s", e)
            return jsonify({"status": "error"}), 500

    @app.route("/webhook/max", methods=["POST"])
    def webhook():
        """Принимает события от MAX. Проверяет подпись секретом из subscriptions."""
        # MAX передаёт secret в заголовке (см. документацию). Если в подписке
        # был указан secret, MAX будет добавлять его. Проверяем строго.
        secret_header = request.headers.get("X-Max-Bot-Api-Secret") or ""
        if Config.WEBHOOK_SECRET:
            if not hmac.compare_digest(secret_header, Config.WEBHOOK_SECRET):
                log.warning("webhook: неверный secret")
                abort(401)

        update = request.get_json(silent=True) or {}
        if not update:
            log.warning("webhook: пустой JSON")
            return jsonify({"ok": False}), 400

        try:
            with session_scope() as s:
                dispatch(update, s, MaxBotClient())
        except Exception as e:
            # Никогда не отдаём 500 в MAX: иначе он будет повторять с экспонентой
            log.exception("webhook handler crash: %s", e)
            return jsonify({"ok": False, "error": "internal"}), 200

        return jsonify({"ok": True})

    return app


# Объект для gunicorn
app = create_app()


if __name__ == "__main__":
    # Локальный запуск
    app.run(host="0.0.0.0", port=Config.PORT, debug=False)
