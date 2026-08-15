"""Generate isolated synthetic CSV/XLSX files used by the Task 10B browser E2E."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from openpyxl import Workbook

HEADERS = (
    "达人名称",
    "达人官方地址",
    "小红书号",
    "联系邮箱",
    "更新时间",
    "粉丝数",
    "达人标签",
)


def row(
    name: str,
    identity: str,
    *,
    followers: int,
    updated_at: str,
    email: str = "",
    tags: str = "task10b|synthetic",
) -> tuple[str, ...]:
    return (
        name,
        f"https://www.xiaohongshu.com/user/profile/{identity}" if identity else "",
        identity,
        email,
        updated_at,
        str(followers),
        tags,
    )


def write_csv(path: Path, rows: list[tuple[str, ...]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        writer.writerows(rows)


def write_xlsx(path: Path, rows: list[tuple[str, ...]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "灰豚主数据"
    worksheet.append(HEADERS)
    for values in rows:
        worksheet.append(values)
    ignored = workbook.create_sheet("非活动说明页")
    ignored.append(["说明", "该 Sheet 不参与本次导入"])
    ignored.append(["公式安全检查", "=1+1"])
    workbook.active = 0
    workbook.save(path)


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "normal-custom-mapping.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["creator_name", "profile", "followers"])
        writer.writerow(
            [
                "Task10B Custom Mapping Epsilon",
                "https://www.xiaohongshu.com/user/profile/task10b-epsilon",
                "321",
            ]
        )
    write_csv(
        output_dir / "normal-stale.csv",
        [
            row(
                "Task10B 超长昵称用于验证真实列表不会截断业务数据但会保持布局稳定 Alpha",
                "task10b-alpha",
                followers=12_345,
                updated_at="2026-05-01 12:00:00",
                email="alpha.task10b@example.test",
                tags="美妆|护肤|敏感肌|成分党|task10b|synthetic",
            ),
            row(
                "Task10B Zero Followers Beta",
                "task10b-beta",
                followers=0,
                updated_at="2026-05-02 12:00:00",
            ),
        ],
    )
    write_xlsx(
        output_dir / "normal-very-stale.xlsx",
        [
            row(
                "Task10B Ten Thousand Gamma",
                "task10b-gamma",
                followers=10_000,
                updated_at="2026-03-01 12:00:00",
                tags="家居|收纳|task10b|synthetic",
            ),
            row(
                "Task10B Missing Contact Delta",
                "task10b-delta",
                followers=86,
                updated_at="2026-03-02 12:00:00",
            ),
        ],
    )
    write_csv(
        output_dir / "normal-duplicate.csv",
        [
            row(
                "Task10B Zero Followers Beta Duplicate",
                "task10b-beta",
                followers=5,
                updated_at="2026-05-03 12:00:00",
            )
        ],
    )
    write_csv(
        output_dir / "normal-invalid.csv",
        [
            row(
                "Task10B Invalid Missing Identity",
                "",
                followers=1,
                updated_at="2026-08-14 12:00:00",
            )
        ],
    )
    write_csv(
        output_dir / "return-fresh.csv",
        [
            row(
                "Task10B Alpha Changed",
                "task10b-alpha",
                followers=22_345,
                updated_at="2026-08-15 12:00:00",
                email="alpha.changed.task10b@example.test",
            ),
            row(
                "Task10B Zero Followers Beta",
                "task10b-beta",
                followers=5,
                updated_at="2026-05-03 12:00:00",
            ),
            row(
                "Task10B Outside Queue Unresolved",
                "task10b-outside-queue",
                followers=777,
                updated_at="2026-08-15 12:00:00",
            ),
        ],
    )
    write_csv(
        output_dir / "return-stale.csv",
        [
            row(
                "Task10B Ten Thousand Gamma Stale Return",
                "task10b-gamma",
                followers=11_000,
                updated_at="2026-04-01 12:00:00",
            )
        ],
    )
    write_csv(
        output_dir / "return-invalid.csv",
        [
            row(
                "Task10B Return Manual Review",
                "",
                followers=2,
                updated_at="2026-08-15 12:00:00",
            )
        ],
    )
    write_csv(
        output_dir / "manual-review-conflict.csv",
        [
            row(
                "Task10B Manual Review Conflict A",
                "task10b-manual-review",
                followers=101,
                updated_at="2026-08-15 12:00:00",
            ),
            row(
                "Task10B Manual Review Conflict B",
                "task10b-manual-review",
                followers=202,
                updated_at="2026-08-15 12:00:00",
            ),
        ],
    )
    print(output_dir.resolve())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    generate(args.output_dir)


if __name__ == "__main__":
    main()
