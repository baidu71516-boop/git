"""Small deterministic normalizers used by source adapters."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

NULL_MARKERS = frozenset({"", "--", "null"})
XHS_PROFILE_PATH = re.compile(r"^/user/profile/([A-Za-z0-9_-]+?)/?$", re.IGNORECASE)
DOUYIN_PROFILE_PATH = re.compile(r"^/user/([^/?#]+?)/?$")
EMAIL_LOCAL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
DOMAIN_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
INTEGER = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)$")
DECIMAL_NUMBER = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$")
RATE_AND_COUNT = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)%\s*\|\s*(\d+|\d{1,3}(?:,\d{3})+)\s*$")
PERCENT_PAIR = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)%\s*\|\s*([+-]?\d+(?:\.\d+)?)%\s*$")
PLAIN_LABELED_PERCENT = re.compile(r"^(.+?)([+-]?\d+(?:\.\d+)?)%$")


@dataclass(frozen=True)
class NormalizedProfile:
    profile_url: str
    normalized_profile_url: str
    platform_account_id: str


@dataclass(frozen=True)
class NormalizedEmail:
    value: str
    normalized_value: str | None
    is_valid: bool


def _canonical_decimal(value: Decimal) -> Decimal:
    return Decimal(format(value.normalize(), "f"))


def normalize_null(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return None if stripped.casefold() in NULL_MARKERS else stripped


def normalize_xhs_profile_url(value: str) -> NormalizedProfile | None:
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not (hostname == "xiaohongshu.com" or hostname.endswith(".xiaohongshu.com"))
    ):
        return None
    path_match = XHS_PROFILE_PATH.fullmatch(parsed.path)
    if path_match is None:
        return None
    account_id = path_match.group(1)
    normalized = f"https://www.xiaohongshu.com/user/profile/{account_id}"
    return NormalizedProfile(
        profile_url=cleaned,
        normalized_profile_url=normalized,
        platform_account_id=account_id,
    )


def normalize_douyin_profile_url(value: str) -> NormalizedProfile | None:
    """Normalize a neutral Douyin profile token, not a UID or sec_uid."""

    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or hostname not in {"douyin.com", "www.douyin.com"}
    ):
        return None
    path_match = DOUYIN_PROFILE_PATH.fullmatch(parsed.path)
    if path_match is None:
        return None
    profile_token = path_match.group(1)
    normalized = f"https://www.douyin.com/user/{profile_token}"
    return NormalizedProfile(
        profile_url=cleaned,
        normalized_profile_url=normalized,
        platform_account_id=profile_token,
    )


def normalize_email(value: str) -> NormalizedEmail | None:
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    if len(cleaned) > 254 or cleaned.count("@") != 1 or any(char.isspace() for char in cleaned):
        return NormalizedEmail(cleaned, None, False)
    local, domain = cleaned.rsplit("@", 1)
    labels = domain.split(".")
    valid = (
        0 < len(local) <= 64
        and EMAIL_LOCAL.fullmatch(local) is not None
        and not local.startswith(".")
        and not local.endswith(".")
        and ".." not in local
        and len(labels) >= 2
        and all(DOMAIN_LABEL.fullmatch(label) is not None for label in labels)
        and len(labels[-1]) >= 2
    )
    normalized = f"{local}@{domain.lower()}" if valid else None
    return NormalizedEmail(cleaned, normalized, valid)


def parse_integer(value: str) -> int | None:
    cleaned = normalize_null(value)
    if cleaned is None or INTEGER.fullmatch(cleaned) is None:
        return None
    try:
        return int(cleaned.replace(",", ""))
    except ValueError:
        return None


def parse_decimal(value: str) -> Decimal | None:
    cleaned = normalize_null(value)
    if cleaned is None or DECIMAL_NUMBER.fullmatch(cleaned) is None:
        return None
    try:
        result = Decimal(cleaned.replace(",", ""))
    except InvalidOperation:
        return None
    return _canonical_decimal(result) if result.is_finite() else None


def parse_percent(value: str) -> Decimal | None:
    cleaned = normalize_null(value)
    if cleaned is None or not cleaned.endswith("%"):
        return None
    percentage = parse_decimal(cleaned[:-1])
    if percentage is None or not Decimal(0) <= percentage <= Decimal(100):
        return None
    return _canonical_decimal(percentage / Decimal(100))


def parse_source_datetime(value: str, timezone_name: str = "Asia/Shanghai") -> datetime | None:
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    normalized = cleaned[:-1] + "+00:00" if cleaned.endswith(("Z", "z")) else cleaned
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(UTC)


def parse_boolean(value: str) -> bool | None:
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    normalized = cleaned.casefold()
    if normalized in {"是", "yes", "true", "1"}:
        return True
    if normalized in {"否", "no", "false", "0"}:
        return False
    return None


def parse_tags(value: str) -> list[str] | None:
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        return []
    return [item.strip() for item in re.split(r"[|,，;；]", cleaned) if item.strip()]


def parse_rate_and_count(value: str) -> tuple[Decimal, int] | None:
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    match = RATE_AND_COUNT.fullmatch(cleaned)
    if match is None:
        return None
    percentage = Decimal(match.group(1))
    if not Decimal(0) <= percentage <= Decimal(100):
        return None
    return _canonical_decimal(percentage / Decimal(100)), int(match.group(2).replace(",", ""))


def parse_percent_pair(value: str) -> tuple[Decimal, Decimal] | None:
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    match = PERCENT_PAIR.fullmatch(cleaned)
    if match is None:
        return None
    first = Decimal(match.group(1))
    second = Decimal(match.group(2))
    if not all(Decimal(0) <= item <= Decimal(100) for item in (first, second)):
        return None
    return (
        _canonical_decimal(first / Decimal(100)),
        _canonical_decimal(second / Decimal(100)),
    )


def parse_labeled_percentages(value: str) -> list[dict[str, str | Decimal]] | None:
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    distribution: list[dict[str, str | Decimal]] = []
    for item in cleaned.split():
        if "|" in item:
            parts = item.split("|")
            if len(parts) != 2 or not parts[0] or not parts[1].endswith("%"):
                return None
            label, percentage_text = parts[0], parts[1][:-1]
        else:
            match = PLAIN_LABELED_PERCENT.fullmatch(item)
            if match is None:
                return None
            label, percentage_text = match.groups()
        percentage = parse_decimal(percentage_text)
        if percentage is None or not Decimal(0) <= percentage <= Decimal(100):
            return None
        distribution.append({"label": label, "rate": _canonical_decimal(percentage / Decimal(100))})
    return distribution or None


__all__ = [
    "NormalizedEmail",
    "NormalizedProfile",
    "normalize_douyin_profile_url",
    "normalize_email",
    "normalize_null",
    "normalize_xhs_profile_url",
    "parse_boolean",
    "parse_decimal",
    "parse_integer",
    "parse_labeled_percentages",
    "parse_percent",
    "parse_percent_pair",
    "parse_rate_and_count",
    "parse_source_datetime",
    "parse_tags",
]
