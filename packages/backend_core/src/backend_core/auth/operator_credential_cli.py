"""Out-of-band exact-ID credential setup for migrated Operators."""

import argparse
import asyncio
import getpass
import sys
from uuid import UUID

from backend_core.auth.service import AuthError, OperatorCredentialSetupService
from backend_core.config import get_settings
from backend_core.db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize one migrated Operator credential by exact identifiers"
    )
    parser.add_argument("--department-id", required=True, type=UUID)
    parser.add_argument("--operator-id", required=True, type=UUID)
    parser.add_argument(
        "--require-super-admin",
        action="store_true",
        help="Require an active Super Admin target and Super Admin Department ceiling",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the Operator password from stdin instead of a secure prompt",
    )
    return parser.parse_args()


def read_password(password_stdin: bool) -> str:
    if password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Operator password: ")
        confirmation = getpass.getpass("Confirm Operator password: ")
        if password != confirmation:
            raise ValueError("Operator passwords do not match")
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Password must contain 12 to 128 characters")
    return password


async def setup(
    department_id: UUID,
    operator_id: UUID,
    password: str,
    *,
    require_super_admin: bool,
) -> None:
    database = Database(get_settings().database_url)
    try:
        async with database.session_factory() as session:
            result = await OperatorCredentialSetupService(session).setup(
                department_id=department_id,
                operator_id=operator_id,
                password=password,
                require_super_admin=require_super_admin,
            )
            print(
                "Initialized Operator credential "
                f"{result.operator.name} ({result.operator.id}); "
                f"revoked {result.revoked_sessions} bound session(s)"
            )
    finally:
        await database.close()


def main() -> None:
    args = parse_args()
    try:
        password = read_password(args.password_stdin)
        asyncio.run(
            setup(
                args.department_id,
                args.operator_id,
                password,
                require_super_admin=args.require_super_admin,
            )
        )
    except (AuthError, ValueError) as exc:
        print(f"setup-operator-credential failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
