"""PostgreSQL physical checks for the additive Phase 3A persistence migration."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from backend_core.config import get_settings
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.schema import CreateSchema, DropSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "infrastructure" / "migrations" / "alembic.ini"
MIGRATIONS = PROJECT_ROOT / "infrastructure" / "migrations"
PHASE3_TABLES = {
    "candidate_pools",
    "targeting_policies",
    "candidate_pool_runs",
    "candidate_pool_members",
    "campaigns",
    "campaign_members",
    "outreach_targets",
    "outreach_tasks",
    "outreach_events",
    "message_templates",
    "message_template_versions",
}


def _test_database_url() -> URL:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    try:
        url = make_url(raw_url)
    except ArgumentError as error:
        pytest.fail(f"TEST_DATABASE_URL is invalid: {error}", pytrace=False)
    if url.get_backend_name() != "postgresql" or "phase1b_test" not in (url.database or "").lower():
        pytest.fail("TEST_DATABASE_URL must be an explicitly named PostgreSQL test database")
    return url.set(drivername="postgresql+psycopg")


def test_phase3a_fresh_upgrade_and_physical_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = _test_database_url()
    schema_name = f"phase3a_persistence_{uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))
    scoped_url = database_url.update_query_dict({"options": f"-csearch_path={schema_name}"})
    monkeypatch.setenv(
        "DATABASE_URL", scoped_url.render_as_string(hide_password=False).replace("%", "%%")
    )
    get_settings.cache_clear()
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS))
    try:
        command.upgrade(config, "head")
        engine = create_engine(scoped_url, pool_pre_ping=True)
        try:
            inspector = inspect(engine)
            assert PHASE3_TABLES <= set(inspector.get_table_names())
            assert "uq_influencer_contact_id_influencer" in {
                item["name"] for item in inspector.get_unique_constraints("influencer_contacts")
            }
            target_checks = {
                item["name"] for item in inspector.get_check_constraints("outreach_targets")
            }
            assert "ck_outreach_target_channel_reference_shape" in target_checks
            with engine.connect() as connection:
                assert connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_trigger "
                        "WHERE tgname = 'trg_outreach_events_append_only' AND NOT tgisinternal)"
                    )
                )
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "0006_phase3a_persistence"
                )
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        admin_engine.dispose()
