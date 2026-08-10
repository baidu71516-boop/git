from backend_core.common.logging import redact


def test_redact_hides_nested_secrets() -> None:
    payload = {
        "username": "operator",
        "smtp_password": "do-not-log",
        "nested": {"APP_MASTER_KEY": "do-not-log-either"},
    }
    assert redact(payload) == {
        "username": "operator",
        "smtp_password": "[REDACTED]",
        "nested": {"APP_MASTER_KEY": "[REDACTED]"},
    }
