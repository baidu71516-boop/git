"""Replaceable storage boundary for original import files."""

import hashlib
import os
import secrets
from collections.abc import AsyncIterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from backend_core.imports.errors import ImportDomainError


@dataclass(frozen=True)
class StoredObject:
    storage_key: str
    sha256: str
    size: int


class StorageAdapter(Protocol):
    async def store(
        self, chunks: AsyncIterable[bytes], *, suffix: str, max_bytes: int
    ) -> StoredObject: ...

    async def read(
        self,
        storage_key: str,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> bytes: ...

    async def delete(self, storage_key: str) -> None: ...


class LocalStorageAdapter:
    def __init__(self, root: Path, *, initialize_root: bool = True) -> None:
        self.root = root.resolve()
        if initialize_root:
            self._ensure_root()

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def _resolve(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if self.root not in candidate.parents:
            raise ImportDomainError("INVALID_STORAGE_KEY", "Invalid storage key")
        return candidate

    async def store(
        self, chunks: AsyncIterable[bytes], *, suffix: str, max_bytes: int
    ) -> StoredObject:
        self._ensure_root()
        safe_suffix = suffix.lower()
        if safe_suffix not in {".csv", ".xlsx"}:
            raise ImportDomainError("INVALID_FILE_EXTENSION", "Only CSV and XLSX are allowed")
        storage_key = f"{secrets.token_hex(16)}{safe_suffix}"
        target = self._resolve(storage_key)
        temporary = self._resolve(f".{storage_key}.upload")
        digest = hashlib.sha256()
        size = 0
        completed = False
        try:
            with temporary.open("xb") as handle:
                temporary.chmod(0o600)
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise ImportDomainError(
                            "FILE_TOO_LARGE",
                            "Import file exceeds size limit",
                            status_code=413,
                        )
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if size == 0:
                raise ImportDomainError("EMPTY_FILE", "Import file is empty")
            temporary.replace(target)
            target.chmod(0o600)
            completed = True
        finally:
            if not completed:
                temporary.unlink(missing_ok=True)
        return StoredObject(storage_key=storage_key, sha256=digest.hexdigest(), size=size)

    async def read(
        self,
        storage_key: str,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> bytes:
        try:
            content = self._resolve(storage_key).read_bytes()
        except FileNotFoundError as exc:
            raise ImportDomainError("FILE_NOT_FOUND", "Stored import file not found") from exc
        if expected_size is not None and len(content) != expected_size:
            raise ImportDomainError("FILE_INTEGRITY_FAILED", "Stored import file size changed")
        if expected_sha256 is not None:
            actual_sha256 = hashlib.sha256(content).hexdigest()
            if not secrets.compare_digest(actual_sha256, expected_sha256):
                raise ImportDomainError("FILE_INTEGRITY_FAILED", "Stored import file hash changed")
        return content

    async def delete(self, storage_key: str) -> None:
        try:
            self._resolve(storage_key).unlink(missing_ok=True)
        except OSError as exc:
            raise ImportDomainError(
                "STORAGE_DELETE_FAILED", "Stored import file cannot be removed"
            ) from exc
