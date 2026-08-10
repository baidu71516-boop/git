"""Source adapters that isolate provider field names from import business rules."""

from collections.abc import Mapping
from typing import Any

from backend_core.imports.contracts import (
    AdaptedRow,
    CanonicalContact,
    CanonicalInfluencerRecord,
    PlatformIdentity,
    RowIssue,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.mappings import (
    HUITUN_DECIMAL_FIELDS,
    HUITUN_INTEGER_FIELDS,
    HUITUN_PERCENT_FIELDS,
    HUITUN_RAW_COMPOSITE_FIELDS,
    huitun_mapping_for_headers,
    validate_mapping,
)
from backend_core.imports.normalizers import (
    normalize_email,
    normalize_null,
    normalize_xhs_profile_url,
    parse_boolean,
    parse_decimal,
    parse_integer,
    parse_labeled_percentages,
    parse_percent,
    parse_percent_pair,
    parse_rate_and_count,
    parse_source_datetime,
    parse_tags,
)
from backend_core.imports.parsers import RawTabularRecord
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    DataSource,
    Platform,
)

PUBLIC_PROFILE_FIELDS = frozenset(
    {
        "bio",
        "gender",
        "region_raw",
        "verification_info",
        "mcn_name",
        "creator_tags",
        "creator_level",
        "is_brand_partner",
    }
)


def _issue(code: str, message: str, field: str) -> RowIssue:
    return RowIssue(code=code, message=message, field=field)


class _MappedAdapter:
    platform = Platform.XIAOHONGSHU
    source: DataSource

    def __init__(self, field_mapping: Mapping[str, str]) -> None:
        self._field_mapping = dict(field_mapping)

    def mapping_for_headers(self, headers: list[str]) -> dict[str, str]:
        return validate_mapping(headers, self._field_mapping)

    def _canonical_values(self, raw_record: RawTabularRecord) -> dict[str, str | None]:
        return {
            canonical: normalize_null(raw_record.values.get(source_field))
            for source_field, canonical in self._field_mapping.items()
            if source_field in raw_record.values
        }

    def adapt(self, raw_record: RawTabularRecord) -> AdaptedRow:
        if not self._field_mapping:
            self.mapping_for_headers(list(raw_record.values))
        values = self._canonical_values(raw_record)
        warnings = [
            RowIssue(
                code=warning.get("code", "PARSER_WARNING"),
                message=warning.get("message", "Parser warning"),
            )
            for warning in raw_record.warnings
        ]
        errors = [
            RowIssue(
                code=error.get("code", "PARSER_ERROR"),
                message=error.get("message", "Parser error"),
            )
            for error in raw_record.errors
        ]

        display_name = values.get("nickname")
        if display_name is None:
            errors.append(_issue("MISSING_REQUIRED_FIELD", "Display name is required", "nickname"))

        supplied_account_id = values.get("platform_account_id")
        external_source_id = values.get("external_source_id")
        raw_profile_url = values.get("profile_url")
        normalized_profile = (
            normalize_xhs_profile_url(raw_profile_url) if raw_profile_url is not None else None
        )
        if raw_profile_url is not None and normalized_profile is None:
            issue = _issue(
                "INVALID_PROFILE_URL",
                "Profile URL is not a valid Xiaohongshu profile URL",
                "profile_url",
            )
            (
                errors if supplied_account_id is None and external_source_id is None else warnings
            ).append(issue)

        extracted_account_id = (
            normalized_profile.platform_account_id if normalized_profile is not None else None
        )
        if (
            supplied_account_id is not None
            and extracted_account_id is not None
            and supplied_account_id != extracted_account_id
        ):
            errors.append(
                _issue(
                    "IDENTITY_CONFLICT",
                    "Mapped platform identity conflicts with the profile URL",
                    "platform_account_id",
                )
            )
        platform_account_id = supplied_account_id or extracted_account_id
        if platform_account_id is None and external_source_id is None:
            errors.append(
                _issue(
                    "MISSING_PLATFORM_IDENTITY",
                    "A platform account ID, external source ID, or valid profile URL is required",
                    "profile_url",
                )
            )

        source_time_value = values.get("source_updated_at")
        source_updated_at = (
            parse_source_datetime(source_time_value) if source_time_value is not None else None
        )
        if source_time_value is not None and source_updated_at is None:
            warnings.append(
                _issue(
                    "INVALID_SOURCE_TIME",
                    "Source update time could not be parsed; freshness will use fill-only rules",
                    "source_updated_at",
                )
            )

        public_profile: dict[str, Any] = {}
        metrics: dict[str, Any] = {}
        self._populate_public_profile(values, public_profile, warnings)
        self._populate_metrics(values, metrics, warnings)

        contacts: tuple[CanonicalContact, ...] = ()
        email_value = values.get("email")
        if email_value is not None:
            email = normalize_email(email_value)
            if email is not None and email.is_valid and email.normalized_value is not None:
                contacts = (
                    CanonicalContact(
                        type=ContactType.EMAIL,
                        value=email.value,
                        normalized_value=email.normalized_value,
                        validation_status=ContactValidationStatus.VALID,
                    ),
                )
            else:
                warnings.append(
                    _issue(
                        "INVALID_EMAIL",
                        "Formal contact email is invalid and will not be stored as a Contact",
                        "email",
                    )
                )

        identity = PlatformIdentity(
            platform=self.platform,
            platform_account_id=platform_account_id,
            account_handle=values.get("account_handle"),
            profile_url=(
                normalized_profile.profile_url if normalized_profile is not None else None
            ),
            normalized_profile_url=(
                normalized_profile.normalized_profile_url
                if normalized_profile is not None
                else None
            ),
            external_source_id=external_source_id,
        )
        record = CanonicalInfluencerRecord(
            display_name=display_name,
            platform_identity=identity,
            source=self.source,
            source_updated_at=source_updated_at,
            public_profile=public_profile,
            metrics=metrics,
            contacts=contacts,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )
        return AdaptedRow(
            row_number=raw_record.row_number,
            raw_data=dict(raw_record.raw_data),
            record=record,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    def _populate_public_profile(
        self,
        values: Mapping[str, str | None],
        output: dict[str, Any],
        warnings: list[RowIssue],
    ) -> None:
        for field in sorted(PUBLIC_PROFILE_FIELDS):
            value = values.get(field)
            if value is None:
                continue
            if field == "is_brand_partner":
                parsed = parse_boolean(value)
                if parsed is None:
                    warnings.append(_issue("INVALID_BOOLEAN", "Boolean value is invalid", field))
                    continue
                output[field] = parsed
            elif field == "creator_tags":
                output[field] = parse_tags(value)
            else:
                output[field] = value

    def _populate_metrics(
        self,
        values: Mapping[str, str | None],
        output: dict[str, Any],
        warnings: list[RowIssue],
    ) -> None:
        for field in sorted(HUITUN_INTEGER_FIELDS):
            self._parse_metric(values, output, warnings, field, parse_integer, "INVALID_INTEGER")
        for field in sorted(HUITUN_DECIMAL_FIELDS):
            self._parse_metric(values, output, warnings, field, parse_decimal, "INVALID_DECIMAL")
        for field in sorted(HUITUN_PERCENT_FIELDS):
            self._parse_metric(values, output, warnings, field, parse_percent, "INVALID_PERCENT")
        for field in sorted(HUITUN_RAW_COMPOSITE_FIELDS):
            value = values.get(field)
            if value is not None:
                output[field] = value
        self._populate_composite_metrics(values, output, warnings)

    @staticmethod
    def _parse_metric(
        values: Mapping[str, str | None],
        output: dict[str, Any],
        warnings: list[RowIssue],
        field: str,
        parser: Any,
        error_code: str,
    ) -> None:
        value = values.get(field)
        if value is None:
            return
        parsed = parser(value)
        if parsed is None or parsed < 0:
            warnings.append(_issue(error_code, "Metric value could not be parsed", field))
            return
        output[field] = parsed

    @staticmethod
    def _populate_composite_metrics(
        values: Mapping[str, str | None],
        output: dict[str, Any],
        warnings: list[RowIssue],
    ) -> None:
        for prefix in ("active_fans", "suspicious_fans"):
            field = f"{prefix}_raw"
            value = values.get(field)
            if value is None:
                continue
            parsed = parse_rate_and_count(value)
            if parsed is None:
                warnings.append(
                    _issue(
                        "INVALID_COMPOSITE_VALUE", "Composite metric was kept only as raw", field
                    )
                )
            else:
                output[f"{prefix}_rate"], output[f"{prefix}_count"] = parsed

        gender_value = values.get("fan_gender_raw")
        if gender_value is not None:
            gender = parse_percent_pair(gender_value)
            if gender is None:
                warnings.append(
                    _issue(
                        "INVALID_COMPOSITE_VALUE",
                        "Fan gender metric was kept only as raw",
                        "fan_gender_raw",
                    )
                )
            else:
                output["fan_male_rate"], output["fan_female_rate"] = gender

        distribution_fields = {
            "fan_region_raw": "fan_region_distribution",
            "fan_age_raw": "fan_age_distribution",
            "fan_active_time_raw": "fan_active_time_distribution",
            "fan_interests_raw": "fan_interests_distribution",
        }
        for field, target in distribution_fields.items():
            value = values.get(field)
            if value is None:
                continue
            distribution = parse_labeled_percentages(value)
            if distribution is None:
                warnings.append(
                    _issue("INVALID_COMPOSITE_VALUE", "Distribution was kept only as raw", field)
                )
            else:
                output[target] = distribution


class HuitunCsvAdapter(_MappedAdapter):
    source = DataSource.HUITUN

    def __init__(self, field_mapping: Mapping[str, str] | None = None) -> None:
        self._explicit_mapping = dict(field_mapping) if field_mapping is not None else None
        super().__init__(field_mapping or {})

    def mapping_for_headers(self, headers: list[str]) -> dict[str, str]:
        mapping = (
            validate_mapping(headers, self._explicit_mapping)
            if self._explicit_mapping is not None
            else huitun_mapping_for_headers(headers)
        )
        self._field_mapping = mapping
        return mapping


class HuitunExcelAdapter(HuitunCsvAdapter):
    """The workbook parser differs; canonical mapping is intentionally identical."""


class GenericCsvAdapter(_MappedAdapter):
    source = DataSource.GENERIC

    def __init__(self, field_mapping: Mapping[str, str]) -> None:
        if not field_mapping:
            raise ImportDomainError("MAPPING_REQUIRED", "Generic CSV requires an explicit mapping")
        super().__init__(field_mapping)


__all__ = ["GenericCsvAdapter", "HuitunCsvAdapter", "HuitunExcelAdapter"]
