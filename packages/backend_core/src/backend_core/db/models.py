"""Import all model modules so Alembic sees complete metadata."""

from backend_core.audit.models import AuditLog
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.content_activity.models import (
    ContentActivityObservation,
    ContentActivityProjection,
    ContentActivityRefreshRequest,
    ProviderAccountIdentity,
    ProviderAccountIdentityVerification,
)
from backend_core.growth.models import (
    Campaign,
    CampaignMember,
    CandidatePool,
    CandidatePoolMember,
    CandidatePoolRun,
    Phase3AIdempotencyRecord,
    TargetingPolicy,
)
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportJobFileClientId,
    ImportRow,
    ImportTaskRequest,
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
from backend_core.outreach.models import (
    MessageTemplate,
    MessageTemplateVersion,
    OutreachEvent,
    OutreachTarget,
    OutreachTask,
)
from backend_core.refresh.models import RefreshQueue, RefreshQueueItem

__all__ = [
    "AuditLog",
    "AuthSession",
    "CandidatePool",
    "CandidatePoolMember",
    "CandidatePoolRun",
    "Campaign",
    "CampaignMember",
    "CollectionJob",
    "ContentActivityObservation",
    "ContentActivityProjection",
    "ContentActivityRefreshRequest",
    "Department",
    "DepartmentPermission",
    "ImportJob",
    "ImportJobFile",
    "ImportJobFileClientId",
    "ImportRow",
    "ImportTaskRequest",
    "Influencer",
    "InfluencerContact",
    "InfluencerCurrentMetrics",
    "InfluencerMetricSnapshot",
    "InfluencerPlatformAccount",
    "InfluencerSourceState",
    "PlatformAccountSourceIdentity",
    "RefreshQueue",
    "RefreshQueueItem",
    "Operator",
    "Phase3AIdempotencyRecord",
    "ProviderAccountIdentity",
    "ProviderAccountIdentityVerification",
    "MessageTemplate",
    "MessageTemplateVersion",
    "OutreachEvent",
    "OutreachTarget",
    "OutreachTask",
    "StoredImportFile",
    "TargetingPolicy",
]
