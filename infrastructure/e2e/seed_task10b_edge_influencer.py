"""Seed one deliberately awkward influencer in the isolated Task 10B database."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from backend_core.db import Database
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    CRMStage,
    DataSource,
    Platform,
)
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerPlatformAccount,
)
from sqlalchemy import select

FIXTURE_NAME = "Task10B Unknown Two Accounts Null Owner"


async def seed() -> None:
    database_url = os.environ.get("TASK10B_DATABASE_URL") or os.environ["DATABASE_URL"]
    database = Database(database_url)
    try:
        async with database.session_factory() as session:
            existing = await session.scalar(
                select(Influencer).where(Influencer.display_name == FIXTURE_NAME)
            )
            if existing is not None:
                raise RuntimeError(f"refusing to overwrite fixture: {FIXTURE_NAME}")

            influencer = Influencer(
                display_name=FIXTURE_NAME,
                owner_operator_id=None,
                crm_stage=CRMStage.TO_DEVELOP,
            )
            session.add(influencer)
            await session.flush()

            accounts = [
                InfluencerPlatformAccount(
                    influencer_id=influencer.id,
                    platform=Platform.XIAOHONGSHU,
                    platform_account_id=f"task10b-unknown-{suffix}",
                    account_name=f"Task10B Unknown Account {suffix.upper()}",
                    account_handle=f"task10b_unknown_{suffix}",
                    profile_url=(
                        "https://www.xiaohongshu.com/user/profile/" f"task10b-unknown-{suffix}"
                    ),
                    normalized_profile_url=(
                        "https://www.xiaohongshu.com/user/profile/" f"task10b-unknown-{suffix}"
                    ),
                    source=DataSource.GENERIC,
                    is_active=True,
                    source_tags=["task10b", "unknown", suffix],
                )
                for suffix in ("a", "b")
            ]
            session.add_all(accounts)
            await session.flush()

            observed_at = datetime.now(UTC)
            session.add(
                InfluencerContact(
                    influencer_id=influencer.id,
                    platform_account_id=accounts[0].id,
                    type=ContactType.EMAIL,
                    value="unknown.edge.task10b@example.test",
                    normalized_value="unknown.edge.task10b@example.test",
                    source=DataSource.MANUAL,
                    validation_status=ContactValidationStatus.VALID,
                    is_current=True,
                    possible_duplicate_contact=False,
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                    source_updated_at=None,
                    first_import_job_id=None,
                    first_import_row_id=None,
                    last_import_job_id=None,
                    last_import_row_id=None,
                )
            )
            await session.commit()
            print(f"seeded edge influencer: {influencer.id}")
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(seed())
