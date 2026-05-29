from unittest.mock import Mock

import pytest


def test_index_returns_service_metadata(monkeypatch):
    monkeypatch.setattr("app.main.Config.validate", lambda: None)
    monkeypatch.setattr("app.main.session_scope", Mock())

    from app.main import create_app

    app = create_app()
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert response.get_json()["service"] == "max-bot-registration"


def test_webhook_rejects_invalid_secret(monkeypatch):
    monkeypatch.setattr("app.main.Config.validate", lambda: None)

    from app.main import create_app

    app = create_app()
    client = app.test_client()

    response = client.post("/webhook/max", json={"update_type": "bot_started"}, headers={"X-Max-Bot-Api-Secret": "wrong"})

    assert response.status_code == 401


def test_webhook_rejects_empty_json(monkeypatch):
    monkeypatch.setattr("app.main.Config.validate", lambda: None)

    from app.main import create_app

    app = create_app()
    client = app.test_client()

    response = client.post("/webhook/max", json={}, headers={"X-Max-Bot-Api-Secret": "test-webhook-secret"})

    assert response.status_code == 400
    assert response.get_json() == {"ok": False}


def test_webhook_dispatch_success(monkeypatch):
    monkeypatch.setattr("app.main.Config.validate", lambda: None)

    fake_session = object()

    class FakeSessionScope:
        def __enter__(self):
            return fake_session

        def __exit__(self, exc_type, exc, tb):
            return False

    dispatch_mock = Mock()
    monkeypatch.setattr("app.main.session_scope", lambda: FakeSessionScope())
    monkeypatch.setattr("app.main.dispatch", dispatch_mock)
    monkeypatch.setattr("app.main.MaxBotClient", lambda: "client")

    from app.main import create_app

    app = create_app()
    client = app.test_client()
    update = {"update_type": "bot_started", "user": {"user_id": 123, "name": "Test"}}

    response = client.post("/webhook/max", json=update, headers={"X-Max-Bot-Api-Secret": "test-webhook-secret"})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    dispatch_mock.assert_called_once_with(update, fake_session, "client")
