import re

from app.services.codes import generate_qr_token, generate_registration_code, render_qr_png, verify_qr_token


def test_generate_registration_code_has_expected_format_and_safe_symbols():
    code = generate_registration_code()

    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{3}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{2}", code)
    assert not set(code.replace("-", "")) & set("IO01")


def test_qr_token_verification_returns_registration_id():
    token = generate_qr_token(42)

    assert verify_qr_token(token) == 42


def test_qr_token_verification_rejects_tampered_token():
    token = generate_qr_token(42)
    tampered = token.replace("42:", "43:", 1)

    assert verify_qr_token(tampered) is None


def test_qr_token_verification_rejects_invalid_format():
    assert verify_qr_token("not-a-valid-token") is None
    assert verify_qr_token("") is None


def test_render_qr_png_returns_png_bytes():
    png = render_qr_png("test-token")

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 100
