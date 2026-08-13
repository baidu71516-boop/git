"""PostgreSQL 16 gates for durable import-task crash recovery.

The existing integration harness creates a random isolated schema in an
explicitly named disposable test database and refuses non-PostgreSQL or
non-16 servers.  Redis is deliberately not a source of truth here: a small
publisher double models a disconnected/restarted broker only after each
database reservation transaction has committed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

import pytest
import test_unified_preview_postgres as preview_gate
from backend_core.config import Settings
from backend_core.imports.enums import ImportJobStatus, ImportTaskKind, ImportTaskState
from backend_core.imports.models import ImportJob, ImportTaskRequest
from backend_core.imports.repository import ImportRepository
from backend_core.imports.task_service import (
    ClaimStatus,
    DispatchStatus,
    ImportTaskService,
    TaskEnvelope,
)
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError


@dataclass(slots=True)
class MutableClock:
    value: datetime = datetime(2026, 8, 13, 5, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@dataclass(slots=True)
class DisconnectablePublisher:
    """Broker-boundary double; PostgreSQL remains the only recovery ledger."""

    connected: bool = False
    attempts: list[UUID] = field(default_factory=list)
    published: list[UUID] = field(default_factory=list)

    def publish(self, envelope: TaskEnvelope) -> None:
        self.attempts.append(envelope.task_token)
        if not self.connected:
            raise ConnectionError("synthetic broker disconnect")
        self.published.append(envelope.task_token)


class SyntheticBusinessFailure(RuntimeError):
    pass


def _settings(
    *,
    dispatch_attempts: int = 5,
    run_attempts: int = 3,
    batch_size: int = 100,
) -> Settings:
    return Settings(
        app_env="test",
        database_url=str(preview_gate.TEST_DATABASE_URL),
        import_task_reconcile_batch_size=batch_size,
        import_task_dispatch_max_attempts=dispatch_attempts,
        import_task_dispatch_backoff_base_seconds=1,
        import_task_dispatch_backoff_max_seconds=1,
        import_task_run_backoff_base_seconds=1,
        import_task_run_backoff_max_seconds=1,
        import_task_lease_seconds=2,
        import_task_heartbeat_seconds=1,
        import_task_preview_max_run_attempts=run_attempts,
    )


async def _job_ids(
    harness: preview_gate.PostgresHarness,
    count: int,
) -> list[UUID]:
    assert count >= 1
    seeded = await preview_gate._seed_batch(harness, [[]])
    result = [seeded.job_id]
    if count == 1:
        return result
    async with harness.factory() as session:
        template = await session.get(ImportJob, seeded.job_id)
        assert template is not None
        for _ in range(count - 1):
            job = ImportJob(
                collection_job_id=template.collection_job_id,
                department_id=template.department_id,
                operator_id=template.operator_id,
                source_type=template.source_type,
                status=ImportJobStatus.DRAFT,
                preview_revision=0,
            )
            session.add(job)
            await session.flush()
            result.append(job.id)
        await session.commit()
    return result


async def _create_preview_task(
    harness: preview_gate.PostgresHarness,
    *,
    job_id: UUID,
    clock: MutableClock,
    settings: Settings,
    reserve_dispatch: bool = True,
) -> TaskEnvelope:
    async with harness.factory() as session:
        service = ImportTaskService(session, settings, clock=clock.now)
        task = await service.create_request(
            task_kind=ImportTaskKind.PREVIEW,
            import_job_id=job_id,
            task_token=uuid4(),
        )
        envelope = TaskEnvelope.from_task(task)
        if reserve_dispatch:
            dispatch = await service.prepare_dispatch(task.task_token)
            assert dispatch.status is DispatchStatus.RESERVED
            assert dispatch.envelope == envelope
        await session.commit()
        return envelope


async def _task(
    harness: preview_gate.PostgresHarness,
    task_token: UUID,
) -> ImportTaskRequest:
    async with harness.factory() as session:
        task = await session.scalar(
            select(ImportTaskRequest).where(ImportTaskRequest.task_token == task_token)
        )
        assert task is not None
        return task


def test_request_crash_broker_disconnect_and_api_restart_republish_same_token() -> None:
    """Close DB-before-publish, publisher failure, Redis, and API restart gaps."""

    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            job_id = (await _job_ids(harness, 1))[0]
            clock = MutableClock()
            settings = _settings()
            envelope = await _create_preview_task(
                harness,
                job_id=job_id,
                clock=clock,
                settings=settings,
            )

            # The API/request process exits here without calling a publisher.
            persisted = await _task(harness, envelope.task_token)
            assert persisted.state is ImportTaskState.REQUESTED
            assert persisted.dispatch_attempts == 1
            assert persisted.last_dispatch_attempt_at == clock.now()
            assert persisted.next_retry_at == clock.now() + timedelta(seconds=1)

            clock.advance(seconds=2)
            async with harness.factory() as session:
                # A fresh service/session is the API restart boundary.
                recovery = await ImportTaskService(session, settings, clock=clock.now).reconcile()
                assert [item.task_token for item in recovery.dispatches] == [envelope.task_token]
                recovered_envelope = recovery.dispatches[0]
                await session.commit()

            publisher = DisconnectablePublisher()
            with pytest.raises(ConnectionError, match="broker disconnect"):
                publisher.publish(recovered_envelope)

            # A disconnected/restarted Redis does not erase the reservation.
            after_disconnect = await _task(harness, envelope.task_token)
            assert after_disconnect.state is ImportTaskState.REQUESTED
            assert after_disconnect.dispatch_attempts == 2
            clock.advance(seconds=2)
            async with harness.factory() as session:
                restarted_api = ImportTaskService(session, settings, clock=clock.now)
                replay = await restarted_api.reconcile()
                assert [item.task_token for item in replay.dispatches] == [envelope.task_token]
                replay_envelope = replay.dispatches[0]
                await session.commit()

            publisher.connected = True
            publisher.publish(replay_envelope)
            assert publisher.attempts == [envelope.task_token, envelope.task_token]
            assert publisher.published == [envelope.task_token]
            assert replay_envelope.broker_kwargs() == {
                "task_token": str(envelope.task_token),
                "import_job_id": str(job_id),
            }
            republished = await _task(harness, envelope.task_token)
            assert republished.dispatch_attempts == 3

    asyncio.run(scenario())


def test_claim_crash_lease_recovery_redispatch_and_bounded_run_exhaustion() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            job_id = (await _job_ids(harness, 1))[0]
            clock = MutableClock()
            settings = _settings(run_attempts=2)
            envelope = await _create_preview_task(
                harness,
                job_id=job_id,
                clock=clock,
                settings=settings,
            )

            # Worker claims and commits the lease, then exits before business DML.
            async with harness.factory() as session:
                claim = await ImportTaskService(session, settings, clock=clock.now).claim(envelope)
                assert claim.status is ClaimStatus.CLAIMED
                assert claim.generation == 1
                await session.commit()

            clock.advance(seconds=3)
            async with harness.factory() as session:
                expired = await ImportTaskService(session, settings, clock=clock.now).reconcile()
                assert [item.task_token for item in expired.deferred] == [envelope.task_token]
                assert expired.dispatches == ()
                await session.commit()
            retry_wait = await _task(harness, envelope.task_token)
            assert retry_wait.state is ImportTaskState.RETRY_WAIT
            assert retry_wait.run_attempts == 1

            clock.advance(seconds=2)
            async with harness.factory() as session:
                due = await ImportTaskService(session, settings, clock=clock.now).reconcile()
                assert [item.task_token for item in due.dispatches] == [envelope.task_token]
                await session.commit()
            async with harness.factory() as session:
                second = await ImportTaskService(session, settings, clock=clock.now).claim(envelope)
                assert second.status is ClaimStatus.CLAIMED
                assert second.generation == 2
                await session.commit()

            # The second lost worker reaches the persisted run limit.
            clock.advance(seconds=3)
            async with harness.factory() as session:
                exhausted = await ImportTaskService(session, settings, clock=clock.now).reconcile()
                assert [item.task_token for item in exhausted.terminal] == [envelope.task_token]
                await session.commit()
            terminal = await _task(harness, envelope.task_token)
            assert terminal.state is ImportTaskState.TERMINAL_FAILED
            assert terminal.run_attempts == 2
            assert terminal.dispatch_attempts == 2
            assert terminal.completed_at == clock.now()

    asyncio.run(scenario())


def test_dispatch_attempts_are_bounded_and_terminal_in_postgres() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            job_id = (await _job_ids(harness, 1))[0]
            clock = MutableClock()
            settings = _settings(dispatch_attempts=2)
            envelope = await _create_preview_task(
                harness,
                job_id=job_id,
                clock=clock,
                settings=settings,
            )
            clock.advance(seconds=2)
            async with harness.factory() as session:
                second = await ImportTaskService(session, settings, clock=clock.now).reconcile()
                assert [item.task_token for item in second.dispatches] == [envelope.task_token]
                await session.commit()
            clock.advance(seconds=2)
            async with harness.factory() as session:
                exhausted = await ImportTaskService(session, settings, clock=clock.now).reconcile()
                assert [item.task_token for item in exhausted.terminal] == [envelope.task_token]
                assert exhausted.dispatches == ()
                await session.commit()
            task = await _task(harness, envelope.task_token)
            assert task.state is ImportTaskState.TERMINAL_FAILED
            assert task.dispatch_attempts == 2
            assert task.run_attempts == 0

    asyncio.run(scenario())


def test_business_transaction_rollback_then_atomic_completion_and_ack_replay() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            job_id = (await _job_ids(harness, 1))[0]
            clock = MutableClock()
            settings = _settings()
            envelope = await _create_preview_task(
                harness,
                job_id=job_id,
                clock=clock,
                settings=settings,
            )
            async with harness.factory() as session:
                claimed = await ImportTaskService(session, settings, clock=clock.now).claim(
                    envelope
                )
                assert claimed.status is ClaimStatus.CLAIMED
                assert claimed.generation == 1
                await session.commit()

            # Business DML and task completion are rolled back together.
            async with harness.factory() as session:
                try:
                    job = await session.get(ImportJob, job_id, with_for_update=True)
                    assert job is not None
                    job.result = {"business_commits": 1}
                    completed = await ImportTaskService(
                        session, settings, clock=clock.now
                    ).complete(envelope.task_token, 1)
                    assert completed is True
                    await session.flush()
                    raise SyntheticBusinessFailure("rollback the whole unit of work")
                except SyntheticBusinessFailure:
                    await session.rollback()

            async with harness.factory() as session:
                job = await session.get(ImportJob, job_id)
                task = await session.scalar(
                    select(ImportTaskRequest).where(
                        ImportTaskRequest.task_token == envelope.task_token
                    )
                )
                assert job is not None and task is not None
                assert job.result is None
                assert task.state is ImportTaskState.RUNNING
                assert task.completed_at is None

            # The retried unit commits the business marker and completed fact once.
            async with harness.factory() as session:
                job = await session.get(ImportJob, job_id, with_for_update=True)
                assert job is not None
                job.result = {"business_commits": 1}
                completed = await ImportTaskService(session, settings, clock=clock.now).complete(
                    envelope.task_token, 1
                )
                assert completed is True
                await session.commit()

            # ACK is lost: duplicate delivery must stop at the completed claim.
            async with harness.factory() as session:
                replay = await ImportTaskService(session, settings, clock=clock.now).claim(envelope)
                assert replay.status is ClaimStatus.COMPLETED
                await session.commit()
            async with harness.factory() as session:
                job = await session.get(ImportJob, job_id)
                assert job is not None
                assert job.result == {"business_commits": 1}
                task_count = await session.scalar(
                    select(func.count())
                    .select_from(ImportTaskRequest)
                    .where(ImportTaskRequest.task_token == envelope.task_token)
                )
                assert task_count == 1
                task = await session.scalar(
                    select(ImportTaskRequest).where(
                        ImportTaskRequest.task_token == envelope.task_token
                    )
                )
                assert task is not None
                assert task.state is ImportTaskState.COMPLETED
                assert task.run_attempts == 1

    asyncio.run(scenario())


def test_concurrent_reconcilers_skip_locked_and_partition_a_bounded_batch() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            job_ids = await _job_ids(harness, 6)
            clock = MutableClock()
            settings = _settings(batch_size=3)
            envelopes = [
                await _create_preview_task(
                    harness,
                    job_id=job_id,
                    clock=clock,
                    settings=settings,
                    reserve_dispatch=False,
                )
                for job_id in job_ids
            ]
            expected = {item.task_token for item in envelopes}
            barrier = asyncio.Barrier(2)

            async def reconcile_one() -> set[UUID]:
                async with harness.factory() as session:
                    result = await ImportTaskService(session, settings, clock=clock.now).reconcile(
                        limit=3
                    )
                    selected = {item.task_token for item in result.dispatches}
                    await barrier.wait()
                    await session.commit()
                    return selected

            first, second = await asyncio.gather(reconcile_one(), reconcile_one())
            assert len(first) == 3
            assert len(second) == 3
            assert first.isdisjoint(second)
            assert first | second == expected
            async with harness.factory() as session:
                attempts = list(
                    await session.scalars(
                        select(ImportTaskRequest.dispatch_attempts).where(
                            ImportTaskRequest.task_token.in_(expected)
                        )
                    )
                )
                assert attempts == [1] * 6

    asyncio.run(scenario())


def test_cancel_and_claim_row_lock_race_has_only_serializable_outcomes() -> None:
    async def claim_in_new_session(
        harness: preview_gate.PostgresHarness,
        envelope: TaskEnvelope,
        settings: Settings,
        clock: MutableClock,
    ) -> ClaimStatus:
        async with harness.factory() as session:
            decision = await ImportTaskService(session, settings, clock=clock.now).claim(envelope)
            await session.commit()
            return decision.status

    async def cancel_in_new_session(
        harness: preview_gate.PostgresHarness,
        task_token: UUID,
        settings: Settings,
        clock: MutableClock,
    ) -> bool:
        async with harness.factory() as session:
            cancelled = await ImportTaskService(session, settings, clock=clock.now).cancel(
                task_token
            )
            await session.commit()
            return cancelled

    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            cancel_job, claim_job = await _job_ids(harness, 2)
            clock = MutableClock()
            settings = _settings()
            cancel_wins = await _create_preview_task(
                harness,
                job_id=cancel_job,
                clock=clock,
                settings=settings,
            )
            claim_wins = await _create_preview_task(
                harness,
                job_id=claim_job,
                clock=clock,
                settings=settings,
            )

            # Cancellation owns the row lock first; a concurrent delivery observes
            # the committed terminal cancellation and cannot run business work.
            async with harness.factory() as cancelling_session:
                cancelled = await ImportTaskService(
                    cancelling_session, settings, clock=clock.now
                ).cancel(cancel_wins.task_token)
                assert cancelled is True
                blocked_claim = asyncio.create_task(
                    claim_in_new_session(harness, cancel_wins, settings, clock)
                )
                await asyncio.sleep(0.05)
                await cancelling_session.commit()
            assert await asyncio.wait_for(blocked_claim, timeout=5) is ClaimStatus.INACTIVE

            # Claim owns the row lock first; cancellation cannot kill running work.
            async with harness.factory() as claiming_session:
                claimed = await ImportTaskService(
                    claiming_session, settings, clock=clock.now
                ).claim(claim_wins)
                assert claimed.status is ClaimStatus.CLAIMED
                blocked_cancel = asyncio.create_task(
                    cancel_in_new_session(harness, claim_wins.task_token, settings, clock)
                )
                await asyncio.sleep(0.05)
                await claiming_session.commit()
            assert await asyncio.wait_for(blocked_cancel, timeout=5) is False

            cancelled_task = await _task(harness, cancel_wins.task_token)
            running_task = await _task(harness, claim_wins.task_token)
            assert cancelled_task.state is ImportTaskState.CANCELLED
            assert cancelled_task.run_attempts == 0
            assert running_task.state is ImportTaskState.RUNNING
            assert running_task.run_attempts == 1

    asyncio.run(scenario())


def test_postgres_heartbeat_renews_lease_and_old_generation_cannot_complete() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            job_id = (await _job_ids(harness, 1))[0]
            clock = MutableClock()
            settings = _settings(run_attempts=3)
            envelope = await _create_preview_task(
                harness,
                job_id=job_id,
                clock=clock,
                settings=settings,
            )
            async with harness.factory() as session:
                first = await ImportTaskService(session, settings, clock=clock.now).claim(envelope)
                assert first.status is ClaimStatus.CLAIMED
                assert first.generation == 1
                await session.commit()
            first_lease = (await _task(harness, envelope.task_token)).lease_expires_at
            assert first_lease == clock.now() + timedelta(seconds=2)

            clock.advance(seconds=1)
            async with harness.factory() as session:
                renewed = await ImportTaskService(session, settings, clock=clock.now).heartbeat(
                    envelope.task_token, 1
                )
                assert renewed is True
                await session.commit()
            renewed_lease = (await _task(harness, envelope.task_token)).lease_expires_at
            assert renewed_lease == clock.now() + timedelta(seconds=2)
            assert renewed_lease > first_lease

            # Once that renewed lease is nevertheless lost, reconciliation
            # creates generation 2 for the same persisted task token.
            clock.advance(seconds=3)
            async with harness.factory() as session:
                expired = await ImportTaskService(session, settings, clock=clock.now).reconcile()
                assert [item.task_token for item in expired.deferred] == [envelope.task_token]
                await session.commit()
            clock.advance(seconds=2)
            async with harness.factory() as session:
                due = await ImportTaskService(session, settings, clock=clock.now).reconcile()
                assert [item.task_token for item in due.dispatches] == [envelope.task_token]
                await session.commit()
            async with harness.factory() as session:
                second = await ImportTaskService(session, settings, clock=clock.now).claim(envelope)
                assert second.status is ClaimStatus.CLAIMED
                assert second.generation == 2
                await session.commit()

            # Generation 1 can neither renew nor turn generation 2's business
            # transaction into a completed fact.
            async with harness.factory() as session:
                stale_service = ImportTaskService(session, settings, clock=clock.now)
                assert await stale_service.heartbeat(envelope.task_token, 1) is False
                assert await stale_service.complete(envelope.task_token, 1) is False
                await session.rollback()

            clock.advance(seconds=1)
            async with harness.factory() as session:
                live_service = ImportTaskService(session, settings, clock=clock.now)
                assert await live_service.heartbeat(envelope.task_token, 2) is True
                assert await live_service.complete(envelope.task_token, 2) is True
                await session.commit()
            completed = await _task(harness, envelope.task_token)
            assert completed.state is ImportTaskState.COMPLETED
            assert completed.run_attempts == 2
            assert completed.completed_at == clock.now()

            # Processor completion is a strict CAS. Broker replay is absorbed
            # by claim(), never by accepting another complete() call.
            async with harness.factory() as session:
                service = ImportTaskService(session, settings, clock=clock.now)
                assert await service.complete(envelope.task_token, 2) is False
                replay = await service.claim(envelope)
                assert replay.status is ClaimStatus.COMPLETED
                await session.commit()

    asyncio.run(scenario())


def test_task_then_job_nowait_conflict_rolls_back_and_releases_task_lock() -> None:
    """The worker's inverse lock path fails fast instead of deadlocking API work."""

    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            job_id = (await _job_ids(harness, 1))[0]
            clock = MutableClock()
            settings = _settings()
            envelope = await _create_preview_task(
                harness,
                job_id=job_id,
                clock=clock,
                settings=settings,
            )

            async with harness.factory() as api_session:
                # API/processor owns the canonical Job -> task lock order.
                job = await ImportRepository(api_session).get_import_job(
                    job_id,
                    for_update=True,
                )
                assert job is not None

                async def inverse_worker_path() -> None:
                    async with harness.factory() as worker_session:
                        try:
                            changed = await ImportTaskService(
                                worker_session, settings, clock=clock.now
                            ).terminal_fail(envelope.task_token)
                            assert changed is True
                            await ImportRepository(worker_session).get_import_job(
                                job_id,
                                for_update=True,
                                nowait=True,
                            )
                        except OperationalError:
                            # This rollback must erase terminal_failed and release
                            # the task lock before the API continues.
                            await worker_session.rollback()
                            raise

                started = perf_counter()
                with pytest.raises(OperationalError):
                    await asyncio.wait_for(inverse_worker_path(), timeout=2)
                assert perf_counter() - started < 2

                cancelled = await ImportTaskService(api_session, settings, clock=clock.now).cancel(
                    envelope.task_token
                )
                assert cancelled is True
                await api_session.commit()

            persisted = await _task(harness, envelope.task_token)
            assert persisted.state is ImportTaskState.CANCELLED
            assert persisted.completed_at == clock.now()
            assert persisted.run_attempts == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["complete", "heartbeat"])
def test_default_database_clock_cas_rechecks_lease_after_row_lock_wait(
    operation: str,
) -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            job_id = (await _job_ids(harness, 1))[0]
            settings = _settings()
            async with harness.factory() as session:
                service = ImportTaskService(session, settings)
                task = await service.create_request(
                    task_kind=ImportTaskKind.PREVIEW,
                    import_job_id=job_id,
                    task_token=uuid4(),
                )
                dispatch = await service.prepare_dispatch(task.task_token)
                assert dispatch.envelope is not None
                envelope = dispatch.envelope
                await session.commit()
            async with harness.factory() as session:
                claim = await ImportTaskService(session, settings).claim(envelope)
                assert claim.status is ClaimStatus.CLAIMED
                assert claim.generation == 1
                await session.commit()
            claimed = await _task(harness, envelope.task_token)
            original_lease = claimed.lease_expires_at
            assert original_lease is not None

            async with harness.factory() as locking_session:
                locked = await ImportTaskService(locking_session, settings).get_by_token(
                    envelope.task_token,
                    for_update=True,
                )
                assert locked is not None and locked.state is ImportTaskState.RUNNING
                loop = asyncio.get_running_loop()
                pid_ready: asyncio.Future[int] = loop.create_future()

                async def blocked_cas() -> bool:
                    async with harness.factory() as waiting_session:
                        pid = int(await waiting_session.scalar(text("SELECT pg_backend_pid()")))
                        pid_ready.set_result(pid)
                        service = ImportTaskService(waiting_session, settings)
                        if operation == "complete":
                            result = await service.complete(envelope.task_token, 1)
                        else:
                            result = await service.heartbeat(envelope.task_token, 1)
                        await waiting_session.commit()
                        return result

                waiting = asyncio.create_task(blocked_cas())
                waiting_pid = await asyncio.wait_for(pid_ready, timeout=2)
                blocked = False
                for _ in range(100):
                    async with harness.factory() as observer:
                        blockers = int(
                            await observer.scalar(
                                text("SELECT cardinality(pg_blocking_pids(:waiting_pid))"),
                                {"waiting_pid": waiting_pid},
                            )
                            or 0
                        )
                    if blockers > 0:
                        blocked = True
                        break
                    await asyncio.sleep(0.01)
                assert blocked is True

                # The CAS started while the lease was valid, but PostgreSQL did
                # not acquire the row until after it expired. The predicate must
                # use the database wall clock at lock recheck, not a timestamp
                # captured by the application before waiting.
                sleep_seconds = max(
                    0.25,
                    (original_lease - datetime.now(UTC)).total_seconds() + 0.25,
                )
                await asyncio.sleep(sleep_seconds)
                assert datetime.now(UTC) > original_lease
                await locking_session.commit()

            assert await asyncio.wait_for(waiting, timeout=3) is False
            persisted = await _task(harness, envelope.task_token)
            assert persisted.state is ImportTaskState.RUNNING
            assert persisted.completed_at is None
            assert persisted.lease_expires_at == original_lease
            assert persisted.lease_expires_at < datetime.now(UTC)

    asyncio.run(scenario())
