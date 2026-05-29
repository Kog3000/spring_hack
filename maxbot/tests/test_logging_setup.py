import logging

from app.logging_setup import PIIRedactingFilter, mask_code, mask_id


def test_mask_id_short_and_long_values():
    assert mask_id(None) == "user:none"
    assert mask_id(123) == "user:123"
    assert mask_id(123456789) == "user:12…89"


def test_mask_code_hides_short_values_and_truncates_long_values():
    assert mask_code("short") == "***"
    assert mask_code("abcdefghijklmnopqrstuvwxyz") == "abcdef…"


def test_pii_redacting_filter_masks_email_and_long_token():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="email ivan.petrov@example.com token abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
        args=(),
        exc_info=None,
    )

    assert PIIRedactingFilter().filter(record) is True
    message = record.getMessage()

    assert "ivan.petrov@example.com" not in message
    assert "iv***@***" in message
    assert "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890" not in message
    assert "abcdef…" in message
