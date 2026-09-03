"""Explicit, exact-scope CLI wrapper for Huitun historical reprojection."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from backend_core.config import get_settings
from backend_core.db import Database
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.huitun_reprojection import HuitunReprojectionService


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reproject one completed, confirmed Huitun import job by exact identifiers"
    )
    parser.add_argument("--department-id", required=True, type=UUID)
    parser.add_argument("--import-job-id", required=True, type=UUID)
    return parser.parse_args(argv)


async def reproject(*, department_id: UUID, import_job_id: UUID) -> dict[str, Any]:
    """Run the production service for exactly one department/job target."""

    database = Database(get_settings().database_url)
    try:
        async with database.session_factory() as session:
            return await HuitunReprojectionService(session).reproject(
                department_id=department_id,
                import_job_id=import_job_id,
            )
    finally:
        await database.close()


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"job id: {summary['import_job_id']}")
    print(f"selected committed rows: {summary['selected_committed_rows']}")
    print(f"processed: {summary['processed']}")
    print(
        "source states repaired/no-op: "
        f"{summary['source_states_repaired']}/{summary['source_states_noop']}"
    )
    print(
        "metric snapshots inserted/no-op: "
        f"{summary['metric_snapshots_inserted']}/{summary['metric_snapshots_noop']}"
    )
    print(f"conflicts/errors: {summary['conflicts']}/{summary['errors']}")


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        summary = asyncio.run(
            reproject(department_id=args.department_id, import_job_id=args.import_job_id)
        )
    except (ImportDomainError, ValueError) as exc:
        code = exc.code if isinstance(exc, ImportDomainError) else "INVALID_ARGUMENT"
        print(f"huitun-reproject failed [{code}]: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    _print_summary(summary)


if __name__ == "__main__":
    main()
