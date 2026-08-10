"""Redis-backed Department + IP login failure throttling."""

import hashlib
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis


@dataclass(frozen=True)
class FailureState:
    count: int
    locked: bool


class LoginThrottleProtocol(Protocol):
    async def is_locked(self, department_id: UUID, ip: str) -> bool: ...

    async def record_failure(self, department_id: UUID, ip: str) -> FailureState: ...

    async def clear(self, department_id: UUID, ip: str) -> None: ...


class RedisLoginThrottle:
    """Use Redis TTL and atomic increment for temporary lockouts."""

    def __init__(self, redis: Redis, *, max_failures: int, lock_seconds: int) -> None:
        self._redis = redis
        self._max_failures = max_failures
        self._lock_seconds = lock_seconds

    def _key(self, department_id: UUID, ip: str) -> str:
        identity_hash = hashlib.sha256(f"{department_id}:{ip}".encode()).hexdigest()
        return f"auth:login-failures:{identity_hash}"

    async def is_locked(self, department_id: UUID, ip: str) -> bool:
        value = await self._redis.get(self._key(department_id, ip))
        return value is not None and int(value) >= self._max_failures

    async def record_failure(self, department_id: UUID, ip: str) -> FailureState:
        key = self._key(department_id, ip)
        count = int(await self._redis.incr(key))
        if count == 1 or count >= self._max_failures:
            await self._redis.expire(key, self._lock_seconds)
        return FailureState(count=count, locked=count >= self._max_failures)

    async def clear(self, department_id: UUID, ip: str) -> None:
        await self._redis.delete(self._key(department_id, ip))
