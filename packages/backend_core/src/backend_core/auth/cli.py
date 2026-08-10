"""One-time bootstrap-admin console command."""

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
        help="Read the initial password from stdin instead of a secure prompt",
    )
    return parser.parse_args()


def read_password(password_stdin: bool) -> str:
    if password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Initial password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise ValueError("Passwords do not match")
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Password must contain 12 to 128 characters")
    return password


async def bootstrap(department_name: str, operator_name: str, password: str) -> None:
    database = Database(get_settings().database_url)
    try:
        async with database.session_factory() as session:
            department, operator = await BootstrapService(session).create_admin(
                department_name=department_name.strip(),
                operator_name=operator_name.strip(),
                password=password,
            )
            print(f"Created admin department {department.name} ({department.id})")
            print(f"Created super admin operator {operator.name} ({operator.id})")
    finally:
        await database.close()


def main() -> None:
    args = parse_args()
    try:
        password = read_password(args.password_stdin)
        asyncio.run(bootstrap(args.department_name, args.operator_name, password))
    except (AuthError, ValueError) as exc:
        print(f"bootstrap-admin failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
