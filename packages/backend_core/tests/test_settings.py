from backend_core.config.settings import Settings
from pydantic import SecretStr


def test_default_business_timezone() -> None:
    settings = Settings(_env_file=None)
    assert settings.business_timezone == "Asia/Shanghai"
    assert settings.import_retention_days == 30
    assert settings.session_default_hours == 12
    assert settings.session_remember_days == 30
    assert settings.login_max_failures == 5
    assert settings.login_lock_seconds == 300
    assert not settings.secure_cookies


def test_production_requires_master_key() -> None:
    try:
        Settings(app_env="production", _env_file=None)
    except ValueError as exc:
        assert "APP_MASTER_KEY" in str(exc)
    else:
        raise AssertionError("production configuration accepted without APP_MASTER_KEY")


def test_production_accepts_injected_master_key() -> None:
    settings = Settings(
        app_env="production",
        app_master_key=SecretStr("injected-at-runtime"),
        _env_file=None,
    )
    assert settings.app_master_key is not None
    assert settings.secure_cookies
