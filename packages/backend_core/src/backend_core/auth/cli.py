"""One-time bootstrap-admin console command with independent credentials."""

import argparse
import asyncio
import getpass
import sys

from backend_core.auth.service import AuthError, BootstrapService
from backend_core.config import get_settings
from backend_core.db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the first administrative department")
    parser.add_argument("--department-name", required=True)
    parser.add_argument("--operator-name", required=True)
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read Department then Operator passwords from two stdin lines",
    )
    return parser.parse_args()


def _validate_password(password: str) -> None:
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Password must contain 12 to 128 characters")


def read_passwords(password_stdin: bool) -> tuple[str, str]:
    if password_stdin:
        department_password = sys.stdin.readline().rstrip("\n")
        operator_password = sys.stdin.readline().rstrip("\n")
    else:
        department_password = getpass.getpass("Department password: ")
        department_confirmation = getpass.getpass("Confirm Department password: ")
        if department_password != department_confirmation:
            raise ValueError("Department passwords do not match")
        operator_password = getpass.getpass("Operator password: ")
        operator_confirmation = getpass.getpass("Confirm Operator password: ")
        if operator_password != operator_confirmation:
            raise ValueError("Operator passwords do not match")
    _validate_password(department_password)
    _validate_password(operator_password)
    if department_password == operator_password:
        raise ValueError("Department and Operator passwords must differ")
    return department_password, operator_password


async def bootstrap(
    department_name: str,
    operator_name: str,
    department_password: str,
    operator_password: str,
) -> None:
    database = Database(get_settings().database_url)
    try:
        async with database.session_factory() as session:
            department, operator = await BootstrapService(session).create_admin(
                department_name=department_name.strip(),
                operator_name=operator_name.strip(),
                department_password=department_password,
                operator_password=operator_password,
            )
            print(f"Created admin department {department.name} ({department.id})")
            print(f"Created super admin operator {operator.name} ({operator.id})")
    finally:
        await database.close()


def main() -> None:
    args = parse_args()
    try:
        department_password, operator_password = read_passwords(args.password_stdin)
        asyncio.run(
            bootstrap(
                args.department_name,
                args.operator_name,
                department_password,
                operator_password,
            )
        )
    except (AuthError, ValueError) as exc:
        print(f"bootstrap-admin failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
