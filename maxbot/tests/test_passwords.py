from app.services.passwords import verify_password, hash_password


def test_hash_password_does_not_store_plain_text():
    password_hash = hash_password("secret123")

    assert password_hash != "secret123"
    assert password_hash.startswith("$2")


def test_verify_password_accepts_correct_password():
    password_hash = hash_password("secret123")

    assert verify_password("secret123", password_hash) is True


def test_verify_password_rejects_wrong_password():
    password_hash = hash_password("secret123")

    assert verify_password("wrong", password_hash) is False
