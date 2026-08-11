"""Import all model modules so Alembic sees complete metadata."""

from backend_core.audit.models import AuditLog
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportRow,
    StoredImportFile,
)
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)

__all__ = [
    "AuditLog",
    "AuthSession",
    "CollectionJob",
    "Department",
    "DepartmentPermission",
    "ImportJob",
    "ImportJobFile",
    "ImportRow",
    "Influencer",
    "InfluencerContact",
    "InfluencerCurrentMetrics",
    "InfluencerMetricSnapshot",
    "InfluencerPlatformAccount",
    "InfluencerSourceState",
    "PlatformAccountSourceIdentity",
    "Operator",
    "StoredImportFile",
]
