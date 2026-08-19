import pytest
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
    assert settings.import_max_batch_rows == 10_000
    assert settings.freshness_fresh_days == 7
    assert settings.freshness_aging_days == 30
    assert settings.freshness_stale_days == 90
    assert not settings.secure_cookies


@pytest.mark.parametrize(
    ("fresh_days", "aging_days", "stale_days"),
    [
        (7, 7, 90),
        (8, 7, 90),
        (7, 30, 30),
        (7, 91, 90),
    ],
)
def test_freshness_thresholds_must_be_strictly_increasing(
    fresh_days: int,
    aging_days: int,
    stale_days: int,
) -> None:
    with pytest.raises(ValueError, match="FRESHNESS_.*strictly increasing"):
        Settings(
            freshness_fresh_days=fresh_days,
            freshness_aging_days=aging_days,
            freshness_stale_days=stale_days,
            _env_file=None,
        )


@pytest.mark.parametrize(
    "app_master_key",
    [None, SecretStr(""), SecretStr(" \t\n ")],
    ids=["missing", "empty", "whitespace"],
)
def test_production_requires_nonblank_master_key(app_master_key: SecretStr | None) -> None:
    with pytest.raises(ValueError, match="APP_MASTER_KEY"):
        Settings(
            app_env="production",
            app_master_key=app_master_key,
            _env_file=None,
        )


def test_production_accepts_injected_master_key() -> None:
    settings = Settings(
        app_env="production",
        app_master_key=SecretStr("injected-at-runtime"),
        _env_file=None,
    )
    assert settings.app_master_key is not None
    assert settings.secure_cookies
