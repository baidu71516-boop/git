"""Database users must see the complete ORM graph in a fresh process."""

import subprocess
import sys


def test_database_import_registers_all_cross_domain_foreign_key_targets() -> None:
    probe = """
from backend_core.db import Base, Database

database = Database("sqlite+aiosqlite://")
expected = {
    "audit_logs",
    "departments",
    "import_jobs",
    "influencer_contacts",
    "influencers",
    "operators",
}
missing = expected.difference(Base.metadata.tables)
assert not missing, sorted(missing)
"""
    subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )
