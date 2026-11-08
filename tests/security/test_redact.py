import logging

from api_sentinel.infrastructure.logging import get_logger
from api_sentinel.infrastructure.security import MASK, redact


def test_redacts_aws_key_and_tokens() -> None:
    text = "key=AKIAIOSFODNN7EXAMPLE token=abc123 password=hunter2"
    result = redact(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in result.text
    assert "abc123" not in result.text and "hunter2" not in result.text
    assert MASK in result.text
    assert not result.clean
    assert {f.kind for f in result.findings} >= {"aws_access_key", "token_param"}


def test_redacts_private_key_and_bearer() -> None:
    text = (
        "Authorization: Bearer abc.def.ghi\n"
        "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"
    )
    out = redact(text).text
    assert "abc.def.ghi" not in out and "PRIVATE KEY-----\nx" not in out


def test_clean_text_passes_through() -> None:
    assert redact("all good").clean


def test_logger_never_writes_secrets(capsys) -> None:
    logger = get_logger("test.sentinel")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    from api_sentinel.infrastructure.logging import _RedactingJsonFormatter

    handler.setFormatter(_RedactingJsonFormatter())
    logger.addHandler(handler)
    logger.info("connecting with token=secretvalue123")
    captured = capsys.readouterr()
    assert "secretvalue123" not in captured.err + captured.out
