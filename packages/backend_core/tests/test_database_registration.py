"""Database users must see the complete ORM graph in a fresh process."""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("entry_path", "entry_module"),
    (
        (None, None),
        (PROJECT_ROOT / "apps" / "api", "app.main"),
        (PROJECT_ROOT / "apps" / "worker", "app.celery_app"),
    ),
    ids=("backend-core", "api", "worker-and-scheduler"),
)
def test_process_entry_registers_all_cross_domain_foreign_key_targets(
    entry_path: Path | None,
    entry_module: str | None,
) -> None:
    entry_path_literal = repr(str(entry_path)) if entry_path is not None else "None"
    probe = f"""
import importlib
import sys
from sqlalchemy.orm import configure_mappers
from backend_core.db import Base, Database

entry_path = {entry_path_literal}
entry_module = {entry_module!r}
if entry_path:
    sys.path.insert(0, entry_path)
if entry_module:
    importlib.import_module(entry_module)

database = Database("sqlite+aiosqlite://")
configure_mappers()
expected = {{
    "audit_logs",
    "candidate_pool_members",
    "candidate_pool_runs",
    "candidate_pools",
    "campaign_members",
    "campaigns",
    "departments",
    "import_job_file_client_ids",
    "import_job_files",
    "import_jobs",
    "import_rows",
    "import_task_requests",
    "influencer_contacts",
    "influencers",
    "message_template_versions",
    "message_templates",
    "operators",
    "outreach_events",
    "outreach_targets",
    "outreach_tasks",
    "refresh_queue_items",
    "refresh_queues",
    "stored_import_files",
    "targeting_policies",
}}
missing = expected.difference(Base.metadata.tables)
assert not missing, sorted(missing)
"""
    subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )
