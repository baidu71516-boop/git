"""Phase 1B collection and import state enums."""

from enum import StrEnum


class CollectionJobStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ImportSourceType(StrEnum):
    MANUAL_HUITUN_EXPORT = "manual_huitun_export"
    GENERIC_CSV = "generic_csv"


class ImportJobStatus(StrEnum):
    DRAFT = "draft"
    UPLOADED = "uploaded"
    PARSING = "parsing"
    MAPPING_REQUIRED = "mapping_required"
    PREVIEWING = "previewing"
    PREVIEW_READY = "preview_ready"
    PREVIEW_STALE = "preview_stale"
    CONFIRM_QUEUED = "confirm_queued"
    IMPORTING = "importing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportJobFailedStage(StrEnum):
    PREVIEW = "preview"
    CONFIRM = "confirm"


class ImportJobFileStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    MAPPING_REQUIRED = "mapping_required"
    READY = "ready"
    FAILED = "failed"
    EXCLUDED = "excluded"


class ImportTaskKind(StrEnum):
    LEGACY_PARSE = "legacy_parse"
    FILE_PARSE = "file_parse"
    PREVIEW = "preview"
    CONFIRM = "confirm"


class ImportTaskState(StrEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    TERMINAL_FAILED = "terminal_failed"
    CANCELLED = "cancelled"


class SourceAcquiredAtOrigin(StrEnum):
    SERVER_DEFAULT = "server_default"
    USER_CONFIRMED = "user_confirmed"
    LEGACY_UNKNOWN = "legacy_unknown"


class ImportRowAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    NO_CHANGE = "no_change"
    SKIP = "skip"
    ERROR = "error"
    MANUAL_REVIEW = "manual_review"


class ImportMatchType(StrEnum):
    PLATFORM_ACCOUNT_ID = "platform_account_id"
    EXTERNAL_SOURCE_ID = "external_source_id"
    NORMALIZED_PROFILE_URL = "normalized_profile_url"
    NONE = "none"


class StoredFileType(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"
