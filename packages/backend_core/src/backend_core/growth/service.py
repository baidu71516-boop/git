"""Candidate Pool policy, run lifecycle, authorization, and materialization service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.auth import BusinessAuthorizationContext as AuthContext
from backend_core.auth.enums import OperatorStatus, Role
from backend_core.auth.repository import AuthRepository
from backend_core.campaigns.access import CampaignOutreachAccess, DepartmentScope
from backend_core.campaigns.errors import CampaignOutreachError
from backend_core.campaigns.schemas import CampaignOwnerSummary
from backend_core.growth.buyer_taxonomy_v1 import (
    buyer_taxonomy_v1_document,
    is_trusted_buyer_taxonomy_v1,
    resolve_buyer_taxonomy_v1,
)
from backend_core.growth.enums import (
    BuyerLeadTier,
    BuyerProspectOwnerFilter,
    CandidatePoolKind,
    CandidatePoolRunStatus,
    CandidatePoolStatus,
    CandidateResult,
    Phase3AOperationScope,
)
from backend_core.growth.long_inactivity import (
    LongInactivityEvidenceSource,
    LongInactivityExecutionCandidate,
    LongInactivityExecutionRequest,
    LongInactivityExecutionResult,
    LongInactivityProviderRefresh,
    ProviderEnrichmentOutcome,
    execute_bounded_long_inactivity_enrichment,
)
from backend_core.growth.models import (
    CandidatePool,
    CandidatePoolRun,
    Phase3AIdempotencyRecord,
    TargetingPolicy,
)
from backend_core.growth.repository import (
    BuyerProspectRuleRecord,
    CandidatePoolMemberProjectionRecord,
    CandidatePoolOwnerRecord,
    CandidatePoolRepository,
)
from backend_core.growth.schemas import (
    BuyerProspectRuleCreateInput,
    BuyerProspectRuleLifecycleInput,
    BuyerProspectRuleOperatorOption,
    BuyerProspectRuleOptionsPublic,
    BuyerProspectRulePage,
    BuyerProspectRulePublic,
    BuyerProspectRuleSourceOption,
    BuyerProspectRuleTaxonomyOption,
    BuyerProspectRuleUpdateInput,
    BuyerScreeningBootstrapPublic,
    CandidatePoolCreateInput,
    CandidatePoolMemberPublic,
    CandidatePoolPage,
    CandidatePoolPublic,
    CandidatePoolRunMemberPage,
    CandidatePoolRunPage,
    CandidatePoolRunPublic,
    CandidatePoolRunRequest,
    LongInactivityEnrichmentRequest,
    TargetingPolicyCreateInput,
    TargetingPolicyCreateResultPublic,
    TargetingPolicyPublic,
    viewer_redacted_evidence,
    viewer_redacted_reason_codes,
)
from backend_core.growth.targeting import (
    BuyerLeadTierDecision,
    BuyerProspectRuleTargetingPolicy,
    BuyerTargetingPolicy,
    CandidateFactBundle,
    SellerTargetingPolicy,
    TargetingEvaluation,
    TargetingEvaluationResult,
    TargetingReasonCode,
    evaluate_buyer_lead_tier,
    evaluate_targeting,
    parse_targeting_policy,
)
from backend_core.imports.enums import CollectionJobStatus
from backend_core.imports.hashing import canonical_json, canonical_value, hash_document
from backend_core.influencers.enums import Platform
from backend_core.influencers.freshness import (
    ContentActivityFreshnessPolicy,
    FreshnessPolicy,
    FreshnessStatus,
    GreyDolphinActivityFreshnessPolicy,
)
from backend_core.influencers.schemas import (
    InfluencerIdentitySummary,
    PlatformAccountIdentitySummary,
)

LongInactivityProviderEnricher = Callable[[UUID], Awaitable[LongInactivityProviderRefresh]]


class TargetingError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _execution_as_of(value: datetime) -> datetime:
    """Normalize a stored Candidate Run instant without changing its clock."""

    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite test storage omits the timezone marker even for the run's UTC
        # DateTime(timezone=True) column.  The durable instant is still the
        # run's authoritative execution clock, not a new "now" value.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


IDEMPOTENCY_RESULT_SCHEMA_VERSION = 1
MAX_IDEMPOTENCY_RESULT_PAYLOAD_BYTES = 16_384


class CandidatePoolService:
    """Own all mutable Candidate Pool operations; the repository never commits."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        freshness_policy: FreshnessPolicy,
        content_activity_freshness_policy: ContentActivityFreshnessPolicy | None = None,
        grey_dolphin_activity_freshness_policy: GreyDolphinActivityFreshnessPolicy | None = None,
        long_inactivity_provider_enricher: LongInactivityProviderEnricher | None = None,
        long_inactivity_provider_budget: int = 0,
    ) -> None:
        self.session = session
        self.repository = CandidatePoolRepository(session)
        self.auth_repository = AuthRepository(session)
        self.access = CampaignOutreachAccess(self.auth_repository)
        self.audit = AuditRepository(session)
        self.freshness_policy = freshness_policy
        # Content Activity freshness is a distinct current-public evidence
        # policy. It must not inherit Huitun/source freshness thresholds.
        self.content_activity_freshness_policy = (
            content_activity_freshness_policy or ContentActivityFreshnessPolicy()
        )
        self.grey_dolphin_activity_freshness_policy = (
            grey_dolphin_activity_freshness_policy or GreyDolphinActivityFreshnessPolicy()
        )
        if long_inactivity_provider_budget < 0:
            raise ValueError("long_inactivity_provider_budget must not be negative")
        self.long_inactivity_provider_enricher = long_inactivity_provider_enricher
        self.long_inactivity_provider_budget = long_inactivity_provider_budget

    @staticmethod
    def _department_id(context: AuthContext) -> UUID:
        return context.department.id

    @classmethod
    def _require_mutation(cls, context: AuthContext) -> UUID:
        if context.operator is None:
            raise TargetingError(409, "OPERATOR_REQUIRED", "Select an operator first")
        if context.effective_role is Role.VIEWER:
            raise TargetingError(403, "PERMISSION_DENIED", "Viewer role is read-only")
        return context.operator.id

    @staticmethod
    def _scope_error(error: CampaignOutreachError) -> TargetingError:
        return TargetingError(error.status_code, error.code, error.message)

    async def _resolve_read_scope(
        self,
        context: AuthContext,
        department_id: UUID | None,
    ) -> DepartmentScope:
        try:
            return await self.access.resolve_read_scope(context, department_id)
        except CampaignOutreachError as error:
            raise self._scope_error(error) from error

    async def _resolve_mutation_scope(
        self,
        context: AuthContext,
        department_id: UUID | None,
    ) -> tuple[DepartmentScope, UUID]:
        try:
            return await self.access.resolve_mutation_scope(context, department_id)
        except CampaignOutreachError as error:
            raise self._scope_error(error) from error

    async def _read_pool(self, pool_id: UUID, scope: DepartmentScope) -> CandidatePool:
        pool = await self.repository.get_pool(
            pool_id,
            department_id=scope.department_id,
        )
        if pool is None:
            raise TargetingError(404, "CANDIDATE_POOL_NOT_FOUND", "Candidate Pool not found")
        return pool

    async def _write_pool(self, pool_id: UUID, scope: DepartmentScope) -> CandidatePool:
        pool = await self.repository.get_pool(
            pool_id,
            department_id=scope.department_id,
            for_update=True,
        )
        if pool is None:
            raise TargetingError(404, "CANDIDATE_POOL_NOT_FOUND", "Candidate Pool not found")
        return pool

    @staticmethod
    def _typed_policy(
        pool: CandidatePool,
        policy: TargetingPolicy,
    ) -> SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy:
        try:
            definition = parse_targeting_policy(policy.definition)
        except (TypeError, ValidationError, ValueError) as error:
            raise TargetingError(
                409, "TARGETING_POLICY_INVALID", "Targeting policy is invalid"
            ) from error
        if (
            policy.schema_version != definition.schema_version
            or policy.canonical_hash != definition.canonical_hash
        ):
            raise TargetingError(409, "TARGETING_POLICY_INVALID", "Targeting policy is invalid")
        if pool.kind is CandidatePoolKind.POTENTIAL_SELLER and isinstance(
            definition, SellerTargetingPolicy
        ):
            return definition
        if pool.kind is CandidatePoolKind.POTENTIAL_BUYER and isinstance(
            definition, (BuyerTargetingPolicy, BuyerProspectRuleTargetingPolicy)
        ):
            return definition
        raise TargetingError(
            409, "POLICY_KIND_MISMATCH", "Policy type does not match Candidate Pool kind"
        )

    @staticmethod
    def _require_reviewed_buyer_taxonomy(
        definition: SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy,
        *,
        status_code: int,
    ) -> None:
        """Require the exact server-owned Buyer V1 artifact for active runs."""

        if not isinstance(definition, (BuyerTargetingPolicy, BuyerProspectRuleTargetingPolicy)):
            return
        if not definition.taxonomy.reviewed:
            raise TargetingError(
                status_code,
                "BUYER_TAXONOMY_UNREVIEWED",
                "Buyer targeting requires a reviewed taxonomy",
            )
        if not is_trusted_buyer_taxonomy_v1(definition.taxonomy):
            raise TargetingError(
                status_code,
                "BUYER_TAXONOMY_UNTRUSTED",
                "Buyer targeting requires the approved Buyer Taxonomy V1 artifact",
            )

    @staticmethod
    def _require_idempotency_key(idempotency_key: str) -> None:
        if not idempotency_key or len(idempotency_key) > 255:
            raise TargetingError(422, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key is invalid")

    @staticmethod
    def _result_payload(
        result: CandidatePoolPublic | TargetingPolicyCreateResultPublic,
    ) -> dict[str, Any]:
        payload = result.model_dump(mode="json")
        if len(canonical_json(payload).encode("utf-8")) > MAX_IDEMPOTENCY_RESULT_PAYLOAD_BYTES:
            raise TargetingError(
                500,
                "IDEMPOTENCY_RESULT_TOO_LARGE",
                "Idempotency result exceeds the configured limit",
            )
        return payload

    @staticmethod
    def _idempotency_key_reused() -> TargetingError:
        return TargetingError(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            "Idempotency-Key was already used for a different request",
        )

    @staticmethod
    def _invalid_idempotency_record() -> TargetingError:
        return TargetingError(
            409,
            "IDEMPOTENCY_RECORD_INVALID",
            "Persisted idempotency result is invalid",
        )

    async def _pool_result_from_idempotency_record(
        self,
        record: Phase3AIdempotencyRecord,
    ) -> CandidatePoolPublic:
        if record.result_schema_version != IDEMPOTENCY_RESULT_SCHEMA_VERSION:
            raise self._invalid_idempotency_record()
        try:
            owner_id = UUID(str(record.result_payload["owner_operator_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise self._invalid_idempotency_record() from error
        owner = await self.auth_repository.get_operator(owner_id)
        if owner is None or owner.department_id != record.department_id:
            raise self._invalid_idempotency_record()
        try:
            result = CandidatePoolPublic.from_replay_payload(
                record.result_payload,
                owner=CampaignOwnerSummary.model_validate(owner),
            )
        except ValidationError as error:
            raise self._invalid_idempotency_record() from error
        if (
            result.id != record.result_entity_id
            or result.department_id != record.department_id
            or result.owner.id != result.owner_operator_id
        ):
            raise self._invalid_idempotency_record()
        return result

    @classmethod
    def _policy_result_from_idempotency_record(
        cls,
        record: Phase3AIdempotencyRecord,
    ) -> TargetingPolicyCreateResultPublic:
        if record.result_schema_version != IDEMPOTENCY_RESULT_SCHEMA_VERSION:
            raise cls._invalid_idempotency_record()
        try:
            result = TargetingPolicyCreateResultPublic.model_validate(record.result_payload)
        except ValidationError as error:
            raise cls._invalid_idempotency_record() from error
        if result.id != record.result_entity_id:
            raise cls._invalid_idempotency_record()
        return result

    async def _pool_create_replay(
        self,
        *,
        department_id: UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> CandidatePoolPublic | None:
        record = await self.repository.get_phase3a_idempotency_record(
            department_id=department_id,
            operation_scope=Phase3AOperationScope.CANDIDATE_POOL_CREATE,
            idempotency_key=idempotency_key,
        )
        if record is None:
            return None
        if record.request_hash != request_hash:
            raise self._idempotency_key_reused()
        return await self._pool_result_from_idempotency_record(record)

    async def _policy_create_replay(
        self,
        *,
        department_id: UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> TargetingPolicyCreateResultPublic | None:
        record = await self.repository.get_phase3a_idempotency_record(
            department_id=department_id,
            operation_scope=Phase3AOperationScope.TARGETING_POLICY_CREATE,
            idempotency_key=idempotency_key,
        )
        if record is None:
            return None
        if record.request_hash != request_hash:
            raise self._idempotency_key_reused()
        return self._policy_result_from_idempotency_record(record)

    @staticmethod
    def _validate_policy_for_pool(
        *,
        kind: CandidatePoolKind,
        source_collection_job_id: UUID | None,
        definition: SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy,
    ) -> None:
        CandidatePoolService._require_reviewed_buyer_taxonomy(definition, status_code=422)
        if kind is CandidatePoolKind.POTENTIAL_SELLER and not isinstance(
            definition, SellerTargetingPolicy
        ):
            raise TargetingError(
                422, "POLICY_KIND_MISMATCH", "Seller pool requires SELLER_V1 policy"
            )
        if kind is CandidatePoolKind.POTENTIAL_BUYER and not isinstance(
            definition, (BuyerTargetingPolicy, BuyerProspectRuleTargetingPolicy)
        ):
            raise TargetingError(422, "POLICY_KIND_MISMATCH", "Buyer pool requires BUYER_V1 policy")
        if kind is CandidatePoolKind.POTENTIAL_BUYER and source_collection_job_id is None:
            raise TargetingError(
                422,
                "SOURCE_COLLECTION_REQUIRED",
                "Buyer Candidate Pools require source_collection_job_id",
            )
        if (
            isinstance(definition, BuyerProspectRuleTargetingPolicy)
            and definition.source_collection_job_id != source_collection_job_id
        ):
            raise TargetingError(
                422,
                "SOURCE_COLLECTION_MISMATCH",
                "Market Prospect Rule source must match its Candidate Pool source",
            )

    @staticmethod
    def _validate_authorable_seller_policy(definition: SellerTargetingPolicy) -> None:
        """Apply the C3A write boundary without changing historical snapshots."""

        if (
            definition.freshness is not None
            and FreshnessStatus.UNKNOWN in definition.freshness.allowed_statuses
        ):
            raise TargetingError(
                422,
                "TARGETING_POLICY_INVALID",
                "New SELLER_V1 policies cannot select unknown freshness",
            )

    @staticmethod
    def _policy_audit_after(policy: TargetingPolicy) -> dict[str, Any]:
        return {
            "pool_id": str(policy.pool_id),
            "version": policy.version,
            "schema_version": policy.schema_version,
            "canonical_hash": policy.canonical_hash,
        }

    @staticmethod
    def _pool_audit_after(pool: CandidatePool) -> dict[str, Any]:
        return {
            "owner_operator_id": str(pool.owner_operator_id),
            "status": pool.status.value,
            "version": pool.version,
            "current_policy_id": str(pool.current_policy_id) if pool.current_policy_id else None,
        }

    @staticmethod
    def _audit_after_with_scope(
        after: dict[str, Any],
        scope: DepartmentScope,
    ) -> dict[str, Any]:
        return {
            **after,
            "cross_department_override": scope.cross_department_override,
        }

    @staticmethod
    def _canonical_create_owner_id(
        *,
        scope: DepartmentScope,
        actor_id: UUID,
        requested_owner_id: UUID | None,
    ) -> UUID:
        """Expand the same-Department owner default before replay lookup."""

        if requested_owner_id is not None:
            return requested_owner_id
        if scope.cross_department_override:
            raise TargetingError(
                409,
                "TARGET_DEPARTMENT_OWNER_REQUIRED",
                "Cross-department Candidate Pool creation requires a target Department owner",
            )
        return actor_id

    async def _owner_operator_id(
        self,
        *,
        scope: DepartmentScope,
        context: AuthContext,
        actor_id: UUID,
        requested_owner_id: UUID | None,
    ) -> UUID:
        selected_target_actor = await self.access.target_department_operator_id(
            context=context,
            target_department_id=scope.department_id,
            fallback_operator_id=actor_id,
        )
        owner_id = requested_owner_id or selected_target_actor
        if owner_id is None:
            raise TargetingError(
                409,
                "TARGET_DEPARTMENT_OWNER_REQUIRED",
                "Cross-department Candidate Pool creation requires a target Department owner",
            )
        owner = await self.auth_repository.get_operator(owner_id)
        if (
            owner is None
            or owner.department_id != scope.department_id
            or owner.status is not OperatorStatus.ACTIVE
        ):
            # Deliberately do not disclose whether a supplied Operator exists in
            # another Department or is merely inactive.
            raise TargetingError(404, "OPERATOR_NOT_FOUND", "Owner operator not found")
        return owner.id

    @staticmethod
    def _market_prospect_definition(
        payload: BuyerProspectRuleCreateInput | BuyerProspectRuleUpdateInput,
    ) -> BuyerProspectRuleTargetingPolicy:
        try:
            return BuyerProspectRuleTargetingPolicy(
                taxonomy=resolve_buyer_taxonomy_v1(),
                category_ids=payload.category_ids,
                follower_min=payload.follower_min,
                follower_max=payload.follower_max,
                buyer_lead_tiers=payload.buyer_lead_tiers,
                source_collection_job_id=payload.source_collection_job_id,
                recent_collection_window=payload.recent_collection_window,
                prospect_owner_filter=payload.prospect_owner_filter,
                prospect_owner_operator_id=payload.prospect_owner_operator_id,
                exclude_contacted=payload.exclude_contacted,
            )
        except ValidationError as error:
            raise TargetingError(
                422,
                "MARKET_PROSPECT_RULE_INVALID",
                "Market Prospect Rule is invalid",
            ) from error

    async def _validate_market_prospect_source(
        self,
        *,
        department_id: UUID,
        source_collection_job_id: UUID,
    ) -> None:
        collection = await self.repository.get_collection_job(
            source_collection_job_id,
            department_id=department_id,
        )
        if collection is None:
            raise TargetingError(404, "COLLECTION_JOB_NOT_FOUND", "Collection Job not found")
        if collection.status not in {CollectionJobStatus.ACTIVE, CollectionJobStatus.COMPLETED}:
            raise TargetingError(
                409,
                "COLLECTION_JOB_INELIGIBLE",
                "Collection Job is not eligible for Market Prospect Rules",
            )
        taxonomy = resolve_buyer_taxonomy_v1()
        if any(
            label is not None and taxonomy.normalize(label) is None
            for label in (collection.industry, collection.subdirection)
        ):
            raise TargetingError(
                409,
                "BUYER_CLIENT_CATEGORY_UNMAPPED",
                "Collection Job client category is not mapped by Buyer Taxonomy V1",
            )
        if not await self.repository.has_committed_buyer_source_candidates(
            collection_job_id=collection.id
        ):
            raise TargetingError(
                409,
                "BUYER_SOURCE_PROVENANCE_INCOMPLETE",
                "Collection Job has no completed committed source provenance for Buyer screening",
            )

    async def _validate_market_prospect_owner_filter(
        self,
        *,
        scope: DepartmentScope,
        policy: BuyerProspectRuleTargetingPolicy,
    ) -> None:
        if policy.prospect_owner_filter is not BuyerProspectOwnerFilter.OPERATOR:
            return
        assert policy.prospect_owner_operator_id is not None
        operator = await self.auth_repository.get_operator(policy.prospect_owner_operator_id)
        if (
            operator is None
            or operator.department_id != scope.department_id
            or operator.status is not OperatorStatus.ACTIVE
        ):
            raise TargetingError(404, "OPERATOR_NOT_FOUND", "Owner operator not found")

    @staticmethod
    def _market_prospect_audit_after(
        *,
        pool: CandidatePool,
        policy: BuyerProspectRuleTargetingPolicy,
        operation: str,
    ) -> dict[str, Any]:
        # Keep the log compact: immutable policy hash/version are audited by
        # the policy event, while this record identifies the employee action.
        return {
            "market_prospect_rule_operation": operation,
            "owner_operator_id": str(pool.owner_operator_id),
            "status": pool.status.value,
            "pool_version": pool.version,
            "policy_type": policy.policy_type,
            "source_collection_job_id": str(policy.source_collection_job_id),
        }

    async def create_pool(
        self,
        context: AuthContext,
        payload: CandidatePoolCreateInput,
        *,
        department_id: UUID | None = None,
        idempotency_key: str,
        ip: str = "unknown",
        user_agent: str = "unknown",
    ) -> CandidatePoolPublic:
        scope, operator_id = await self._resolve_mutation_scope(context, department_id)
        resolved_department_id = scope.department_id
        self._require_idempotency_key(idempotency_key)
        mutation_started = False
        try:
            typed_definition = parse_targeting_policy(payload.policy)
            self._validate_policy_for_pool(
                kind=payload.kind,
                source_collection_job_id=payload.source_collection_job_id,
                definition=typed_definition,
            )
            if isinstance(typed_definition, SellerTargetingPolicy):
                self._validate_authorable_seller_policy(typed_definition)
            if payload.kind is CandidatePoolKind.POTENTIAL_BUYER:
                assert payload.source_collection_job_id is not None
                collection = await self.repository.get_collection_job(
                    payload.source_collection_job_id,
                    department_id=resolved_department_id,
                )
                if collection is None:
                    raise TargetingError(
                        404,
                        "COLLECTION_JOB_NOT_FOUND",
                        "Collection Job not found",
                    )
            canonical_owner_id = self._canonical_create_owner_id(
                scope=scope,
                actor_id=operator_id,
                requested_owner_id=payload.owner_operator_id,
            )
            request_hash = hash_document(
                {
                    "operation": "candidate_pool_create",
                    "department_id": resolved_department_id,
                    "owner_operator_id": canonical_owner_id,
                    "name": payload.name,
                    "kind": payload.kind,
                    "source_collection_job_id": payload.source_collection_job_id,
                    "policy": typed_definition.model_dump(mode="json"),
                }
            )
            replay = await self._pool_create_replay(
                department_id=resolved_department_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                await self.session.commit()
                return replay
            owner_operator_id = await self._owner_operator_id(
                scope=scope,
                context=context,
                actor_id=operator_id,
                requested_owner_id=payload.owner_operator_id,
            )
            mutation_started = True
            pool = CandidatePool(
                department_id=resolved_department_id,
                owner_operator_id=owner_operator_id,
                name=payload.name,
                kind=payload.kind,
                source_collection_job_id=payload.source_collection_job_id,
                status=CandidatePoolStatus.ACTIVE,
                current_policy_id=None,
                version=1,
            )
            self.session.add(pool)
            await self.session.flush()
            policy = await self._append_policy_locked(pool, operator_id, typed_definition)
            await self.session.refresh(pool)
            await self.session.refresh(policy)
            projection = await self.repository.get_pool_with_owner(
                pool.id,
                department_id=resolved_department_id,
            )
            if projection is None:
                raise TargetingError(
                    500,
                    "CANDIDATE_POOL_OWNER_PROJECTION_INVALID",
                    "Candidate Pool owner projection is invalid",
                )
            result = self._pool_public_from_record(projection)
            self.audit.add(
                action=AuditAction.CANDIDATE_POOL_CREATED,
                result=AuditResult.SUCCESS,
                department_id=resolved_department_id,
                operator_id=operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="candidate_pool",
                entity_id=pool.id,
                after=self._audit_after_with_scope(self._pool_audit_after(pool), scope),
            )
            self.audit.add(
                action=AuditAction.TARGETING_POLICY_CREATED,
                result=AuditResult.SUCCESS,
                department_id=resolved_department_id,
                operator_id=operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="targeting_policy",
                entity_id=policy.id,
                after=self._audit_after_with_scope(self._policy_audit_after(policy), scope),
            )
            self.session.add(
                Phase3AIdempotencyRecord(
                    department_id=resolved_department_id,
                    operation_scope=Phase3AOperationScope.CANDIDATE_POOL_CREATE,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_entity_id=pool.id,
                    result_schema_version=IDEMPOTENCY_RESULT_SCHEMA_VERSION,
                    result_payload=self._result_payload(result),
                )
            )
            await self.session.flush()
            await self.session.commit()
            return result
        except IntegrityError:
            await self.session.rollback()
            try:
                replay = await self._pool_create_replay(
                    department_id=resolved_department_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    await self.session.commit()
                    return replay
            except BaseException:
                await self.session.rollback()
                raise
            await self.session.rollback()
            raise
        except TargetingError:
            if mutation_started:
                await self.session.rollback()
            else:
                await self.session.commit()
            raise
        except BaseException:
            await self.session.rollback()
            raise

    @staticmethod
    def _buyer_bootstrap_name(collection_name: str) -> str:
        suffix = " · 潜客筛选"
        return f"{collection_name[: 200 - len(suffix)]}{suffix}"

    async def bootstrap_buyer_screening(
        self,
        context: AuthContext,
        *,
        collection_job_id: UUID,
        department_id: UUID | None = None,
        idempotency_key: str,
        ip: str = "unknown",
        user_agent: str = "unknown",
    ) -> BuyerScreeningBootstrapPublic:
        """Create or reuse the one server-owned Buyer screening for a CollectionJob.

        The CollectionJob row lock serializes repeated clicks. This endpoint has
        no client-provided policy surface: the trusted frozen taxonomy is the
        sole source for the first immutable Buyer policy.
        """

        scope, operator_id = await self._resolve_mutation_scope(context, department_id)
        self._require_idempotency_key(idempotency_key)
        mutation_started = False
        try:
            collection = await self.repository.get_collection_job(
                collection_job_id,
                department_id=scope.department_id,
                for_update=True,
            )
            if collection is None:
                raise TargetingError(404, "COLLECTION_JOB_NOT_FOUND", "Collection Job not found")

            policy_definition = BuyerTargetingPolicy(taxonomy=resolve_buyer_taxonomy_v1())
            if any(
                label is not None and policy_definition.taxonomy.normalize(label) is None
                for label in (collection.industry, collection.subdirection)
            ):
                raise TargetingError(
                    409,
                    "BUYER_CLIENT_CATEGORY_UNMAPPED",
                    "Collection Job client category is not mapped by Buyer Taxonomy V1",
                )
            if not await self.repository.has_committed_buyer_source_candidates(
                collection_job_id=collection.id
            ):
                raise TargetingError(
                    409,
                    "BUYER_SOURCE_PROVENANCE_INCOMPLETE",
                    "Collection Job has no completed committed source provenance "
                    "for Buyer screening",
                )

            pool = await self.repository.get_active_buyer_pool_for_bootstrap(
                department_id=scope.department_id,
                collection_job_id=collection.id,
                policy_hash=policy_definition.canonical_hash,
            )
            reused_existing_pool = pool is not None
            if pool is None:
                mutation_started = True
                pool = CandidatePool(
                    department_id=scope.department_id,
                    owner_operator_id=operator_id,
                    name=self._buyer_bootstrap_name(collection.name),
                    kind=CandidatePoolKind.POTENTIAL_BUYER,
                    source_collection_job_id=collection.id,
                    status=CandidatePoolStatus.ACTIVE,
                    current_policy_id=None,
                    version=1,
                )
                self.session.add(pool)
                await self.session.flush()
                policy_record = await self._append_policy_locked(
                    pool,
                    operator_id,
                    policy_definition,
                )
                self.audit.add(
                    action=AuditAction.CANDIDATE_POOL_CREATED,
                    result=AuditResult.SUCCESS,
                    department_id=scope.department_id,
                    operator_id=operator_id,
                    ip=ip,
                    user_agent=user_agent,
                    entity_type="candidate_pool",
                    entity_id=pool.id,
                    after=self._audit_after_with_scope(self._pool_audit_after(pool), scope),
                )
                self.audit.add(
                    action=AuditAction.TARGETING_POLICY_CREATED,
                    result=AuditResult.SUCCESS,
                    department_id=scope.department_id,
                    operator_id=operator_id,
                    ip=ip,
                    user_agent=user_agent,
                    entity_type="targeting_policy",
                    entity_id=policy_record.id,
                    after=self._audit_after_with_scope(
                        self._policy_audit_after(policy_record), scope
                    ),
                )
            else:
                policy_record = await self.repository.get_current_policy(pool, for_update=True)
                if policy_record is None:
                    raise TargetingError(
                        409,
                        "TARGETING_POLICY_REQUIRED",
                        "Candidate Pool has no policy",
                    )
                existing_definition = self._typed_policy(pool, policy_record)
                self._require_reviewed_buyer_taxonomy(existing_definition, status_code=409)

            typed_policy = self._typed_policy(pool, policy_record)
            if not isinstance(typed_policy, BuyerTargetingPolicy):
                raise TargetingError(
                    409,
                    "POLICY_KIND_MISMATCH",
                    "Buyer Pool requires BUYER_V1 policy",
                )
            run = await self.repository.get_first_run_for_policy(
                pool_id=pool.id,
                policy_id=policy_record.id,
            )
            if run is None:
                mutation_started = True
                run = await self._stage_run_locked(
                    pool=pool,
                    policy_record=policy_record,
                    policy=typed_policy,
                    operator_id=operator_id,
                    scope=scope,
                    idempotency_key=idempotency_key,
                    request_hash=hash_document(
                        {
                            "operation": "buyer_screening_bootstrap",
                            "collection_job_id": collection.id,
                            "policy_hash": typed_policy.canonical_hash,
                        }
                    ),
                    ip=ip,
                    user_agent=user_agent,
                )

            projection = await self.repository.get_pool_with_owner(
                pool.id,
                department_id=scope.department_id,
            )
            if projection is None:
                raise TargetingError(
                    500,
                    "CANDIDATE_POOL_OWNER_PROJECTION_INVALID",
                    "Candidate Pool owner projection is invalid",
                )
            result = BuyerScreeningBootstrapPublic(
                pool=self._pool_public_from_record(projection),
                policy=TargetingPolicyCreateResultPublic.model_validate(policy_record),
                run=CandidatePoolRunPublic.model_validate(run),
                reused_existing_pool=reused_existing_pool,
            )
            await self.session.commit()
            return result
        except TargetingError:
            if mutation_started:
                await self.session.rollback()
            else:
                await self.session.commit()
            raise
        except BaseException:
            await self.session.rollback()
            raise

    async def create_buyer_prospect_rule(
        self,
        context: AuthContext,
        payload: BuyerProspectRuleCreateInput,
        *,
        department_id: UUID | None = None,
        idempotency_key: str,
        ip: str = "unknown",
        user_agent: str = "unknown",
    ) -> BuyerProspectRulePublic:
        """Create one closed Market Prospect Rule backed by a Buyer Pool."""

        scope, _operator_id = await self._resolve_mutation_scope(context, department_id)
        policy = self._market_prospect_definition(payload)
        try:
            await self._validate_market_prospect_source(
                department_id=scope.department_id,
                source_collection_job_id=policy.source_collection_job_id,
            )
            await self._validate_market_prospect_owner_filter(scope=scope, policy=policy)
            created = await self.create_pool(
                context,
                CandidatePoolCreateInput(
                    name=payload.name,
                    kind=CandidatePoolKind.POTENTIAL_BUYER,
                    source_collection_job_id=policy.source_collection_job_id,
                    owner_operator_id=payload.owner_operator_id,
                    policy=policy,
                ),
                department_id=scope.department_id,
                idempotency_key=idempotency_key,
                ip=ip,
                user_agent=user_agent,
            )
            return await self.get_buyer_prospect_rule(
                context,
                pool_id=created.id,
                department_id=scope.department_id,
            )
        except TargetingError:
            await self.session.rollback()
            raise

    async def list_buyer_prospect_rules(
        self,
        context: AuthContext,
        *,
        cursor: UUID | None,
        limit: int,
        include_archived: bool,
        department_id: UUID | None = None,
    ) -> BuyerProspectRulePage:
        scope = await self._resolve_read_scope(context, department_id)
        records, next_cursor = await self.repository.list_buyer_prospect_rules(
            department_id=scope.department_id,
            cursor=cursor,
            limit=limit,
            include_archived=include_archived,
        )
        return BuyerProspectRulePage(
            items=tuple(self._buyer_prospect_rule_public(record) for record in records),
            next_cursor=next_cursor,
        )

    async def get_buyer_prospect_rule(
        self,
        context: AuthContext,
        *,
        pool_id: UUID,
        department_id: UUID | None = None,
    ) -> BuyerProspectRulePublic:
        scope = await self._resolve_read_scope(context, department_id)
        record = await self.repository.get_buyer_prospect_rule(
            pool_id=pool_id,
            department_id=scope.department_id,
        )
        if record is None:
            raise TargetingError(
                404, "BUYER_PROSPECT_RULE_NOT_FOUND", "Market Prospect Rule not found"
            )
        return self._buyer_prospect_rule_public(record)

    async def buyer_prospect_rule_options(
        self,
        context: AuthContext,
        *,
        department_id: UUID | None = None,
    ) -> BuyerProspectRuleOptionsPublic:
        scope = await self._resolve_read_scope(context, department_id)
        collections = await self.repository.list_buyer_prospect_source_collection_jobs(
            department_id=scope.department_id
        )
        operators = await self.auth_repository.list_active_operators(scope.department_id)
        taxonomy = resolve_buyer_taxonomy_v1()
        eligible_collections = tuple(
            collection
            for collection in collections
            if all(
                label is None or taxonomy.normalize(label) is not None
                for label in (collection.industry, collection.subdirection)
            )
        )
        taxonomy_categories = tuple(
            BuyerProspectRuleTaxonomyOption(id=node["id"], label=node["display_name"])
            for node in buyer_taxonomy_v1_document()["nodes"]
        )
        return BuyerProspectRuleOptionsPublic(
            taxonomy_category_ids=taxonomy.categories,
            taxonomy_categories=taxonomy_categories,
            source_collection_jobs=tuple(
                BuyerProspectRuleSourceOption(
                    id=collection.id,
                    name=collection.name,
                    industry=collection.industry,
                    subdirection=collection.subdirection,
                )
                for collection in eligible_collections
            ),
            operators=tuple(
                BuyerProspectRuleOperatorOption(id=operator.id, name=operator.name)
                for operator in operators
            ),
        )

    async def update_buyer_prospect_rule(
        self,
        context: AuthContext,
        *,
        pool_id: UUID,
        payload: BuyerProspectRuleUpdateInput,
        department_id: UUID | None = None,
        ip: str = "unknown",
        user_agent: str = "unknown",
    ) -> BuyerProspectRulePublic:
        scope, operator_id = await self._resolve_mutation_scope(context, department_id)
        next_definition = self._market_prospect_definition(payload)
        mutation_started = False
        try:
            record = await self.repository.get_buyer_prospect_rule(
                pool_id=pool_id,
                department_id=scope.department_id,
                for_update=True,
            )
            if record is None:
                raise TargetingError(
                    404,
                    "BUYER_PROSPECT_RULE_NOT_FOUND",
                    "Market Prospect Rule not found",
                )
            pool = record.pool
            if pool.version != payload.expected_pool_version:
                raise TargetingError(
                    409,
                    "VERSION_CONFLICT",
                    "Market Prospect Rule changed; refresh before saving",
                )
            current_definition = self._typed_policy(pool, record.policy)
            if not isinstance(current_definition, BuyerProspectRuleTargetingPolicy):
                raise TargetingError(
                    409,
                    "POLICY_KIND_MISMATCH",
                    "Candidate Pool is not a Market Prospect Rule",
                )
            await self._validate_market_prospect_source(
                department_id=scope.department_id,
                source_collection_job_id=next_definition.source_collection_job_id,
            )
            await self._validate_market_prospect_owner_filter(
                scope=scope,
                policy=next_definition,
            )
            owner_operator_id = await self._owner_operator_id(
                scope=scope,
                context=context,
                actor_id=operator_id,
                requested_owner_id=payload.owner_operator_id,
            )
            functional_change = next_definition.canonical_hash != current_definition.canonical_hash
            metadata_change = (
                pool.name != payload.name or pool.owner_operator_id != owner_operator_id
            )
            if not functional_change and not metadata_change:
                await self.session.commit()
                return self._buyer_prospect_rule_public(record)

            before = self._pool_audit_after(pool)
            mutation_started = True
            pool.name = payload.name
            pool.owner_operator_id = owner_operator_id
            if functional_change:
                pool.source_collection_job_id = next_definition.source_collection_job_id
                policy_record = await self._append_policy_locked(
                    pool,
                    operator_id,
                    next_definition,
                )
            else:
                policy_record = record.policy
                pool.version += 1
                await self.session.flush()
            self.audit.add(
                action=AuditAction.CANDIDATE_POOL_UPDATED,
                result=AuditResult.SUCCESS,
                department_id=pool.department_id,
                operator_id=operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="candidate_pool",
                entity_id=pool.id,
                before=before,
                after=self._audit_after_with_scope(
                    self._market_prospect_audit_after(
                        pool=pool,
                        policy=next_definition,
                        operation=("functional_edit" if functional_change else "metadata_edit"),
                    ),
                    scope,
                ),
            )
            if functional_change:
                self.audit.add(
                    action=AuditAction.TARGETING_POLICY_CREATED,
                    result=AuditResult.SUCCESS,
                    department_id=pool.department_id,
                    operator_id=operator_id,
                    ip=ip,
                    user_agent=user_agent,
                    entity_type="targeting_policy",
                    entity_id=policy_record.id,
                    after=self._audit_after_with_scope(
                        self._policy_audit_after(policy_record), scope
                    ),
                )
            await self.session.commit()
            updated = await self.repository.get_buyer_prospect_rule(
                pool_id=pool.id,
                department_id=scope.department_id,
            )
            if updated is None:
                raise TargetingError(
                    500,
                    "BUYER_PROSPECT_RULE_PROJECTION_INVALID",
                    "Market Prospect Rule projection is invalid",
                )
            return self._buyer_prospect_rule_public(updated)
        except TargetingError:
            if mutation_started:
                await self.session.rollback()
            else:
                await self.session.commit()
            raise
        except BaseException:
            await self.session.rollback()
            raise

    async def set_buyer_prospect_rule_lifecycle(
        self,
        context: AuthContext,
        *,
        pool_id: UUID,
        payload: BuyerProspectRuleLifecycleInput,
        department_id: UUID | None = None,
        ip: str = "unknown",
        user_agent: str = "unknown",
    ) -> BuyerProspectRulePublic:
        scope, operator_id = await self._resolve_mutation_scope(context, department_id)
        mutation_started = False
        try:
            record = await self.repository.get_buyer_prospect_rule(
                pool_id=pool_id,
                department_id=scope.department_id,
                for_update=True,
            )
            if record is None:
                raise TargetingError(
                    404,
                    "BUYER_PROSPECT_RULE_NOT_FOUND",
                    "Market Prospect Rule not found",
                )
            pool = record.pool
            policy = self._typed_policy(pool, record.policy)
            if not isinstance(policy, BuyerProspectRuleTargetingPolicy):
                raise TargetingError(
                    409,
                    "POLICY_KIND_MISMATCH",
                    "Candidate Pool is not a Market Prospect Rule",
                )
            if pool.version != payload.expected_pool_version:
                raise TargetingError(
                    409,
                    "VERSION_CONFLICT",
                    "Market Prospect Rule changed; refresh before updating status",
                )
            if (
                pool.status is CandidatePoolStatus.ARCHIVED
                and payload.status is not CandidatePoolStatus.ARCHIVED
            ):
                raise TargetingError(
                    409,
                    "CANDIDATE_POOL_ARCHIVED",
                    "Archived Market Prospect Rules cannot be re-enabled",
                )
            if pool.status is payload.status:
                await self.session.commit()
                return self._buyer_prospect_rule_public(record)
            before = self._pool_audit_after(pool)
            mutation_started = True
            pool.status = payload.status
            pool.version += 1
            await self.session.flush()
            self.audit.add(
                action=AuditAction.CANDIDATE_POOL_UPDATED,
                result=AuditResult.SUCCESS,
                department_id=pool.department_id,
                operator_id=operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="candidate_pool",
                entity_id=pool.id,
                before=before,
                after=self._audit_after_with_scope(
                    self._market_prospect_audit_after(
                        pool=pool,
                        policy=policy,
                        operation=(
                            "enabled"
                            if payload.status is CandidatePoolStatus.ACTIVE
                            else (
                                "disabled"
                                if payload.status is CandidatePoolStatus.DISABLED
                                else "archived"
                            )
                        ),
                    ),
                    scope,
                ),
            )
            await self.session.commit()
            updated = await self.repository.get_buyer_prospect_rule(
                pool_id=pool.id,
                department_id=scope.department_id,
            )
            if updated is None:
                raise TargetingError(
                    500,
                    "BUYER_PROSPECT_RULE_PROJECTION_INVALID",
                    "Market Prospect Rule projection is invalid",
                )
            return self._buyer_prospect_rule_public(updated)
        except TargetingError:
            if mutation_started:
                await self.session.rollback()
            else:
                await self.session.commit()
            raise
        except BaseException:
            await self.session.rollback()
            raise

    async def append_policy(
        self,
        context: AuthContext,
        pool_id: UUID,
        payload: TargetingPolicyCreateInput,
        *,
        department_id: UUID | None = None,
        idempotency_key: str,
        ip: str = "unknown",
        user_agent: str = "unknown",
    ) -> TargetingPolicyCreateResultPublic:
        scope, operator_id = await self._resolve_mutation_scope(context, department_id)
        resolved_department_id = scope.department_id
        self._require_idempotency_key(idempotency_key)
        mutation_started = False
        try:
            preflight_pool = await self.repository.get_pool(
                pool_id,
                department_id=resolved_department_id,
            )
            if preflight_pool is None:
                raise TargetingError(404, "CANDIDATE_POOL_NOT_FOUND", "Candidate Pool not found")
            typed_definition = parse_targeting_policy(payload.policy)
            self._validate_policy_for_pool(
                kind=preflight_pool.kind,
                source_collection_job_id=preflight_pool.source_collection_job_id,
                definition=typed_definition,
            )
            if isinstance(typed_definition, SellerTargetingPolicy):
                self._validate_authorable_seller_policy(typed_definition)
            request_hash = hash_document(
                {
                    "operation": "targeting_policy_create",
                    "pool_id": pool_id,
                    "policy": typed_definition.model_dump(mode="json"),
                }
            )
            replay = await self._policy_create_replay(
                department_id=resolved_department_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                await self.session.commit()
                return replay
            pool = await self._write_pool(pool_id, scope)
            before = self._pool_audit_after(pool)
            mutation_started = True
            policy = await self._append_policy_locked(pool, operator_id, typed_definition)
            await self.session.refresh(pool)
            await self.session.refresh(policy)
            result = TargetingPolicyCreateResultPublic.model_validate(policy)
            self.audit.add(
                action=AuditAction.CANDIDATE_POOL_UPDATED,
                result=AuditResult.SUCCESS,
                department_id=resolved_department_id,
                operator_id=operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="candidate_pool",
                entity_id=pool.id,
                before=before,
                after=self._audit_after_with_scope(self._pool_audit_after(pool), scope),
            )
            self.audit.add(
                action=AuditAction.TARGETING_POLICY_CREATED,
                result=AuditResult.SUCCESS,
                department_id=resolved_department_id,
                operator_id=operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="targeting_policy",
                entity_id=policy.id,
                after=self._audit_after_with_scope(self._policy_audit_after(policy), scope),
            )
            self.session.add(
                Phase3AIdempotencyRecord(
                    department_id=resolved_department_id,
                    operation_scope=Phase3AOperationScope.TARGETING_POLICY_CREATE,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_entity_id=policy.id,
                    result_schema_version=IDEMPOTENCY_RESULT_SCHEMA_VERSION,
                    result_payload=self._result_payload(result),
                )
            )
            await self.session.flush()
            await self.session.commit()
            return result
        except IntegrityError:
            await self.session.rollback()
            try:
                replay = await self._policy_create_replay(
                    department_id=resolved_department_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    await self.session.commit()
                    return replay
            except BaseException:
                await self.session.rollback()
                raise
            await self.session.rollback()
            raise
        except TargetingError:
            if mutation_started:
                await self.session.rollback()
            else:
                await self.session.commit()
            raise
        except BaseException:
            await self.session.rollback()
            raise

    async def _append_policy_locked(
        self,
        pool: CandidatePool,
        operator_id: UUID,
        definition: SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy,
    ) -> TargetingPolicy:
        typed_definition = parse_targeting_policy(definition)
        self._validate_policy_for_pool(
            kind=pool.kind,
            source_collection_job_id=pool.source_collection_job_id,
            definition=typed_definition,
        )
        policy = TargetingPolicy(
            pool_id=pool.id,
            version=await self.repository.next_policy_version(pool.id),
            schema_version=typed_definition.schema_version,
            definition=typed_definition.model_dump(mode="json"),
            canonical_hash=typed_definition.canonical_hash,
            created_by_operator_id=operator_id,
        )
        self.session.add(policy)
        await self.session.flush()
        pool.current_policy_id = policy.id
        pool.version += 1
        await self.session.flush()
        return policy

    async def list_pools(
        self,
        context: AuthContext,
        *,
        cursor: UUID | None,
        limit: int,
        department_id: UUID | None = None,
    ) -> CandidatePoolPage:
        scope = await self._resolve_read_scope(context, department_id)
        page = await self.repository.list_pools(
            department_id=scope.department_id,
            cursor=cursor,
            limit=limit,
        )
        return CandidatePoolPage(
            items=tuple(self._pool_public_from_record(item) for item in page.items),
            next_cursor=page.next_cursor,
        )

    async def get_pool(
        self,
        context: AuthContext,
        pool_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> CandidatePoolPublic:
        scope = await self._resolve_read_scope(context, department_id)
        projection = await self.repository.get_pool_with_owner(
            pool_id,
            department_id=scope.department_id,
        )
        if projection is None:
            raise TargetingError(404, "CANDIDATE_POOL_NOT_FOUND", "Candidate Pool not found")
        return self._pool_public_from_record(projection)

    async def list_policies(
        self,
        context: AuthContext,
        pool_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> tuple[TargetingPolicyPublic, ...]:
        scope = await self._resolve_read_scope(context, department_id)
        pool = await self._read_pool(pool_id, scope)
        return tuple(
            TargetingPolicyPublic.model_validate(item)
            for item in await self.repository.list_policies(pool.id)
        )

    async def get_policy(
        self,
        context: AuthContext,
        *,
        pool_id: UUID,
        policy_id: UUID,
        department_id: UUID | None = None,
    ) -> TargetingPolicyPublic:
        scope = await self._resolve_read_scope(context, department_id)
        pool = await self._read_pool(pool_id, scope)
        policy = await self.repository.get_policy(policy_id, pool_id=pool.id)
        if policy is None:
            raise TargetingError(404, "TARGETING_POLICY_NOT_FOUND", "Targeting policy not found")
        return TargetingPolicyPublic.model_validate(policy)

    async def list_runs(
        self,
        context: AuthContext,
        *,
        pool_id: UUID,
        cursor: UUID | None,
        limit: int,
        department_id: UUID | None = None,
    ) -> CandidatePoolRunPage:
        scope = await self._resolve_read_scope(context, department_id)
        pool = await self._read_pool(pool_id, scope)
        page = await self.repository.list_runs(pool_id=pool.id, cursor=cursor, limit=limit)
        return CandidatePoolRunPage(
            items=tuple(CandidatePoolRunPublic.model_validate(item) for item in page.items),
            next_cursor=page.next_cursor,
        )

    @staticmethod
    def _run_requested_audit_after(run: CandidatePoolRun) -> dict[str, Any]:
        return {
            "pool_id": str(run.pool_id),
            "policy_id": str(run.policy_id),
            "status": run.status.value,
        }

    @staticmethod
    def _run_terminal_audit_after(run: CandidatePoolRun) -> dict[str, Any]:
        return {
            "pool_id": str(run.pool_id),
            "policy_id": str(run.policy_id),
            "status": run.status.value,
            "match_count": run.match_count,
            "unknown_count": run.unknown_count,
            "not_match_count": run.not_match_count,
            "error_code": run.error_code,
        }

    def _add_run_audit(
        self,
        *,
        action: AuditAction,
        result: AuditResult,
        run: CandidatePoolRun,
        department_id: UUID,
        ip: str,
        user_agent: str,
        after: dict[str, Any],
        operator_id: UUID | None = None,
    ) -> None:
        self.audit.add(
            action=action,
            result=result,
            department_id=department_id,
            operator_id=operator_id,
            ip=ip,
            user_agent=user_agent,
            entity_type="candidate_pool_run",
            entity_id=run.id,
            after=after,
        )

    async def _mark_run_failed(
        self,
        *,
        run: CandidatePoolRun,
        pool: CandidatePool,
        error_code: str,
        error_message: str,
    ) -> CandidatePoolRun:
        try:
            run.status = CandidatePoolRunStatus.FAILED
            run.error_code = error_code
            run.error_message = error_message
            self._add_run_audit(
                action=AuditAction.CANDIDATE_POOL_RUN_FAILED,
                result=AuditResult.FAILED,
                run=run,
                department_id=pool.department_id,
                ip="worker",
                user_agent="celery:materialize_candidate_pool_run",
                after=self._run_terminal_audit_after(run),
            )
            await self.session.commit()
            return run
        except BaseException:
            await self.session.rollback()
            raise

    async def _stage_run_locked(
        self,
        *,
        pool: CandidatePool,
        policy_record: TargetingPolicy,
        policy: SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy,
        operator_id: UUID,
        scope: DepartmentScope,
        idempotency_key: str,
        request_hash: str,
        long_inactivity_enrichment: LongInactivityEnrichmentRequest | None = None,
        ip: str,
        user_agent: str,
    ) -> CandidatePoolRun:
        """Create one durable PENDING Run from the already-locked policy snapshot."""

        as_of = _utc_now()
        watermark = await self.repository.capture_input_watermark(pool=pool, policy=policy)
        watermark.update(
            {
                "policy_id": policy_record.id,
                "policy_version": policy_record.version,
                "policy_hash": policy_record.canonical_hash,
                "as_of": as_of,
            }
        )
        if long_inactivity_enrichment is not None:
            watermark["long_inactivity_enrichment"] = {
                "request": long_inactivity_enrichment.model_dump(mode="json")
            }
        run = CandidatePoolRun(
            pool_id=pool.id,
            policy_id=policy_record.id,
            as_of=as_of,
            input_watermark=canonical_value(watermark),
            status=CandidatePoolRunStatus.PENDING,
            match_count=0,
            unknown_count=0,
            not_match_count=0,
            error_code=None,
            error_message=None,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        self.session.add(run)
        await self.session.flush()
        self._add_run_audit(
            action=AuditAction.CANDIDATE_POOL_RUN_REQUESTED,
            result=AuditResult.SUCCESS,
            run=run,
            department_id=pool.department_id,
            operator_id=operator_id,
            ip=ip,
            user_agent=user_agent,
            after=self._audit_after_with_scope(self._run_requested_audit_after(run), scope),
        )
        return run

    async def reserve_run(
        self,
        context: AuthContext,
        *,
        pool_id: UUID,
        payload: CandidatePoolRunRequest | None = None,
        department_id: UUID | None = None,
        idempotency_key: str,
        ip: str = "unknown",
        user_agent: str = "unknown",
    ) -> CandidatePoolRunPublic:
        """Reserve a current, historical, or adjusted-policy Run.

        The Pool row is the concurrency boundary. In particular, adjusted
        reruns look up an idempotent replay before applying their compare-and-
        swap check, then append a new policy, change the Pool pointer, and add
        a PENDING Run in the same transaction.
        """

        scope, operator_id = await self._resolve_mutation_scope(context, department_id)
        self._require_idempotency_key(idempotency_key)
        request = payload or CandidatePoolRunRequest()
        mutation_started = False
        try:
            pool = await self._write_pool(pool_id, scope)

            if request.is_adjusted_policy_run:
                assert request.base_policy_id is not None
                assert request.expected_pool_version is not None
                assert request.policy is not None
                requested_policy = request.policy
                self._validate_authorable_seller_policy(requested_policy)
                request_hash = hash_document(
                    {
                        "operation": "candidate_pool_adjusted_run",
                        "pool_id": pool.id,
                        "base_policy_id": request.base_policy_id,
                        "expected_pool_version": request.expected_pool_version,
                        "policy": requested_policy.model_dump(mode="json"),
                    }
                )
                existing = await self.repository.get_run_by_idempotency_key(
                    pool_id=pool.id,
                    idempotency_key=idempotency_key,
                    for_update=True,
                )
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise TargetingError(
                            409,
                            "IDEMPOTENCY_CONFLICT",
                            "Idempotency-Key was already used for a different request",
                        )
                    response = CandidatePoolRunPublic.model_validate(existing).model_copy(
                        update={"idempotent_replay": True}
                    )
                    await self.session.commit()
                    return response
                if (
                    request.expected_pool_version != pool.version
                    or request.base_policy_id != pool.current_policy_id
                ):
                    raise TargetingError(
                        409,
                        "VERSION_CONFLICT",
                        "Candidate Pool policy changed; refresh before adjusting and rerunning",
                    )
                if pool.status is not CandidatePoolStatus.ACTIVE:
                    raise TargetingError(
                        409, "CANDIDATE_POOL_INACTIVE", "Candidate Pool is not active"
                    )
                base_policy = await self.repository.get_current_policy(pool, for_update=True)
                if base_policy is None:
                    raise TargetingError(
                        409, "TARGETING_POLICY_REQUIRED", "Candidate Pool has no policy"
                    )
                typed_base_policy = self._typed_policy(pool, base_policy)
                if not isinstance(typed_base_policy, SellerTargetingPolicy):
                    raise TargetingError(
                        409,
                        "POLICY_KIND_MISMATCH",
                        "Only the current SELLER_V1 policy can be adjusted",
                    )
                if requested_policy.canonical_hash == base_policy.canonical_hash:
                    raise TargetingError(
                        422,
                        "TARGETING_POLICY_UNCHANGED",
                        "Adjusted policy is identical to the current policy",
                    )
                before = self._pool_audit_after(pool)
                mutation_started = True
                policy_record = await self._append_policy_locked(
                    pool,
                    operator_id,
                    requested_policy,
                )
                self.audit.add(
                    action=AuditAction.CANDIDATE_POOL_UPDATED,
                    result=AuditResult.SUCCESS,
                    department_id=pool.department_id,
                    operator_id=operator_id,
                    ip=ip,
                    user_agent=user_agent,
                    entity_type="candidate_pool",
                    entity_id=pool.id,
                    before=before,
                    after=self._audit_after_with_scope(self._pool_audit_after(pool), scope),
                )
                self.audit.add(
                    action=AuditAction.TARGETING_POLICY_CREATED,
                    result=AuditResult.SUCCESS,
                    department_id=pool.department_id,
                    operator_id=operator_id,
                    ip=ip,
                    user_agent=user_agent,
                    entity_type="targeting_policy",
                    entity_id=policy_record.id,
                    after=self._audit_after_with_scope(
                        self._policy_audit_after(policy_record), scope
                    ),
                )
                policy: (
                    SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy
                ) = requested_policy
            else:
                if request.is_current_policy_run or request.is_long_inactivity_current_policy_run:
                    selected_policy_record = await self.repository.get_current_policy(
                        pool,
                        for_update=True,
                    )
                    operation = (
                        "candidate_pool_run_current_policy_with_long_inactivity_enrichment"
                        if request.is_long_inactivity_current_policy_run
                        else "candidate_pool_run_current_policy"
                    )
                else:
                    assert request.policy_id is not None
                    selected_policy_record = await self.repository.get_policy(
                        request.policy_id,
                        pool_id=pool.id,
                        for_update=True,
                    )
                    operation = "candidate_pool_run_historical_policy"
                if selected_policy_record is None:
                    raise TargetingError(
                        404 if request.is_historical_policy_run else 409,
                        (
                            "TARGETING_POLICY_NOT_FOUND"
                            if request.is_historical_policy_run
                            else "TARGETING_POLICY_REQUIRED"
                        ),
                        (
                            "Targeting policy not found"
                            if request.is_historical_policy_run
                            else "Candidate Pool has no policy"
                        ),
                    )
                policy_record = selected_policy_record
                policy = self._typed_policy(pool, policy_record)
                if request.long_inactivity_enrichment is not None and (
                    not isinstance(policy, SellerTargetingPolicy) or policy.long_inactivity is None
                ):
                    raise TargetingError(
                        422,
                        "LONG_INACTIVITY_ENRICHMENT_UNAVAILABLE",
                        "Long Inactivity enrichment requires a Seller Long Inactivity policy",
                    )
                request_hash = hash_document(
                    {
                        "operation": operation,
                        "pool_id": pool.id,
                        "policy_id": policy_record.id,
                        "policy_hash": policy.canonical_hash,
                        "long_inactivity_enrichment": (
                            request.long_inactivity_enrichment.model_dump(mode="json")
                            if request.long_inactivity_enrichment is not None
                            else None
                        ),
                    }
                )
                existing = await self.repository.get_run_by_idempotency_key(
                    pool_id=pool.id,
                    idempotency_key=idempotency_key,
                    for_update=True,
                )
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise TargetingError(
                            409,
                            "IDEMPOTENCY_CONFLICT",
                            "Idempotency-Key was already used for a different request",
                        )
                    response = CandidatePoolRunPublic.model_validate(existing).model_copy(
                        update={"idempotent_replay": True}
                    )
                    await self.session.commit()
                    return response
                if pool.status is not CandidatePoolStatus.ACTIVE:
                    raise TargetingError(
                        409, "CANDIDATE_POOL_INACTIVE", "Candidate Pool is not active"
                    )
                self._require_reviewed_buyer_taxonomy(policy, status_code=409)
                mutation_started = True

            run = await self._stage_run_locked(
                pool=pool,
                policy_record=policy_record,
                policy=policy,
                operator_id=operator_id,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                long_inactivity_enrichment=request.long_inactivity_enrichment,
                ip=ip,
                user_agent=user_agent,
            )
            await self.session.commit()
            await self.session.refresh(run)
            return CandidatePoolRunPublic.model_validate(run)
        except TargetingError:
            if mutation_started:
                await self.session.rollback()
            else:
                # Rejections and replay checks are read-only. Commit releases
                # the Pool/run row locks without expiring authenticated context.
                await self.session.commit()
            raise
        except BaseException:
            await self.session.rollback()
            raise

    async def get_run(
        self,
        context: AuthContext,
        *,
        pool_id: UUID,
        run_id: UUID,
        department_id: UUID | None = None,
    ) -> CandidatePoolRunPublic:
        scope = await self._resolve_read_scope(context, department_id)
        pool = await self._read_pool(pool_id, scope)
        run = await self.repository.get_run(run_id, pool_id=pool.id)
        if run is None:
            raise TargetingError(
                404, "CANDIDATE_POOL_RUN_NOT_FOUND", "Candidate Pool run not found"
            )
        return CandidatePoolRunPublic.model_validate(run)

    async def list_run_members(
        self,
        context: AuthContext,
        *,
        pool_id: UUID,
        run_id: UUID,
        cursor: UUID | None,
        limit: int,
        result: CandidateResult | None = None,
        buyer_lead_tier: BuyerLeadTier | None = None,
        department_id: UUID | None = None,
    ) -> CandidatePoolRunMemberPage:
        scope = await self._resolve_read_scope(context, department_id)
        pool = await self._read_pool(pool_id, scope)
        run = await self.repository.get_run(run_id, pool_id=pool.id)
        if run is None:
            raise TargetingError(
                404, "CANDIDATE_POOL_RUN_NOT_FOUND", "Candidate Pool run not found"
            )
        if buyer_lead_tier is not None and pool.kind is not CandidatePoolKind.POTENTIAL_BUYER:
            raise TargetingError(
                422,
                "BUYER_LEAD_TIER_FILTER_INVALID",
                "Buyer lead tier filtering requires a Buyer Candidate Pool",
            )
        page = await self.repository.list_run_members(
            pool_id=pool.id,
            department_id=scope.department_id,
            run_id=run.id,
            cursor=cursor,
            limit=limit,
            result=result,
            buyer_lead_tier=buyer_lead_tier,
        )
        viewer = context.effective_role is Role.VIEWER
        return CandidatePoolRunMemberPage(
            items=tuple(self._member_public(item, viewer=viewer) for item in page.items),
            next_cursor=page.next_cursor,
        )

    @staticmethod
    def _pool_public_from_record(record: CandidatePoolOwnerRecord) -> CandidatePoolPublic:
        return CandidatePoolPublic.from_model(
            record.pool,
            owner=CampaignOwnerSummary.model_validate(record.owner),
        )

    def _buyer_prospect_rule_public(
        self,
        record: BuyerProspectRuleRecord,
    ) -> BuyerProspectRulePublic:
        """Project only the current immutable Market policy for employee views."""

        pool = record.pool
        definition = self._typed_policy(pool, record.policy)
        if (
            not isinstance(definition, BuyerProspectRuleTargetingPolicy)
            or pool.current_policy_id != record.policy.id
            or pool.source_collection_job_id != definition.source_collection_job_id
        ):
            raise TargetingError(
                500,
                "BUYER_PROSPECT_RULE_PROJECTION_INVALID",
                "Market Prospect Rule projection is invalid",
            )
        return BuyerProspectRulePublic(
            id=pool.id,
            department_id=pool.department_id,
            owner_operator_id=pool.owner_operator_id,
            owner=CampaignOwnerSummary.model_validate(record.owner),
            name=pool.name,
            status=pool.status,
            version=pool.version,
            current_policy_id=record.policy.id,
            current_policy_version=record.policy.version,
            category_ids=definition.category_ids,
            follower_min=definition.follower_min,
            follower_max=definition.follower_max,
            buyer_lead_tiers=definition.buyer_lead_tiers,
            source_collection_job_id=definition.source_collection_job_id,
            recent_collection_window=definition.recent_collection_window,
            prospect_owner_filter=definition.prospect_owner_filter,
            prospect_owner_operator_id=definition.prospect_owner_operator_id,
            exclude_contacted=definition.exclude_contacted,
            latest_run=(
                CandidatePoolRunPublic.model_validate(record.latest_run)
                if record.latest_run is not None
                else None
            ),
            created_at=pool.created_at,
            updated_at=pool.updated_at,
        )

    @staticmethod
    def _member_public(
        record: CandidatePoolMemberProjectionRecord,
        *,
        viewer: bool,
    ) -> CandidatePoolMemberPublic:
        member = record.member
        if record.platform_account.influencer_id != member.influencer_id:
            raise TargetingError(
                500,
                "CANDIDATE_POOL_MEMBER_PROJECTION_INVALID",
                "Candidate Pool member account projection is invalid",
            )
        evidence = member.redacted_evidence
        if viewer:
            evidence = viewer_redacted_evidence(evidence)
        reason_codes = tuple(TargetingReasonCode(code) for code in member.reason_codes)
        return CandidatePoolMemberPublic.from_projection(
            member,
            influencer=InfluencerIdentitySummary.model_validate(record.influencer),
            platform_account=PlatformAccountIdentitySummary.model_validate(record.platform_account),
            reason_codes=(viewer_redacted_reason_codes(reason_codes) if viewer else reason_codes),
            redacted_evidence=evidence,
        )

    @staticmethod
    def _long_inactivity_enrichment_request(
        run: CandidatePoolRun,
    ) -> LongInactivityEnrichmentRequest | None:
        raw_watermark = run.input_watermark
        if not isinstance(raw_watermark, dict):
            return None
        raw_execution = raw_watermark.get("long_inactivity_enrichment")
        if raw_execution is None:
            return None
        if not isinstance(raw_execution, dict):
            raise TargetingError(
                409,
                "LONG_INACTIVITY_ENRICHMENT_INVALID",
                "Long Inactivity enrichment request is invalid",
            )
        try:
            return LongInactivityEnrichmentRequest.model_validate(raw_execution["request"])
        except (KeyError, TypeError, ValidationError) as error:
            raise TargetingError(
                409,
                "LONG_INACTIVITY_ENRICHMENT_INVALID",
                "Long Inactivity enrichment request is invalid",
            ) from error

    @staticmethod
    def _long_inactivity_result_and_source(
        evaluation: TargetingEvaluation,
    ) -> tuple[TargetingEvaluationResult, LongInactivityEvidenceSource]:
        criteria = evaluation.redacted_evidence.get("criteria")
        if not isinstance(criteria, list):
            raise ValueError("long inactivity criterion is missing")
        for criterion in criteria:
            if not isinstance(criterion, dict) or criterion.get("criterion") != "long_inactivity":
                continue
            try:
                result = TargetingEvaluationResult(criterion["result"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("long inactivity result is invalid") from error
            observed = criterion.get("observed")
            source = observed.get("source") if isinstance(observed, dict) else None
            if result is TargetingEvaluationResult.UNKNOWN:
                return result, LongInactivityEvidenceSource.UNKNOWN
            if source == LongInactivityEvidenceSource.TRUSTED_CONTENT_ACTIVITY.value:
                return result, LongInactivityEvidenceSource.TRUSTED_CONTENT_ACTIVITY
            if source == LongInactivityEvidenceSource.HUITUN_DOUYIN_AWEME_LIST.value:
                return result, LongInactivityEvidenceSource.HUITUN_DOUYIN_AWEME_LIST
            if source == LongInactivityEvidenceSource.GREY_DOLPHIN.value:
                return result, LongInactivityEvidenceSource.GREY_DOLPHIN
            raise ValueError("resolved long inactivity evidence source is invalid")
        raise ValueError("long inactivity criterion is missing")

    def _evaluate_targeting(
        self,
        policy: SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy,
        facts: CandidateFactBundle,
        *,
        as_of: datetime,
    ) -> TargetingEvaluation:
        return evaluate_targeting(
            policy,
            facts,
            as_of=as_of,
            content_activity_freshness_policy=self.content_activity_freshness_policy,
            grey_dolphin_activity_freshness_policy=(self.grey_dolphin_activity_freshness_policy),
        )

    def _evaluation_record(
        self,
        policy: SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy,
        facts: CandidateFactBundle,
        *,
        as_of: datetime,
    ) -> tuple[CandidateFactBundle, TargetingEvaluation, BuyerLeadTierDecision | None]:
        evaluation = self._evaluate_targeting(policy, facts, as_of=as_of)
        if isinstance(policy, BuyerProspectRuleTargetingPolicy):
            # Market rules store only selected matches and insufficient rows.
            # Excluded rows remain counted on the Run but never leak into the
            # employee result view as an apparent prospect list.
            decision = evaluate_buyer_lead_tier(policy.buyer_tier_policy(), facts)
            return (
                facts,
                evaluation,
                (
                    decision
                    if evaluation.result is not TargetingEvaluationResult.NOT_MATCH
                    else None
                ),
            )
        return (
            facts,
            evaluation,
            (
                evaluate_buyer_lead_tier(policy, facts)
                if isinstance(policy, BuyerTargetingPolicy)
                else None
            ),
        )

    async def _execute_long_inactivity_enrichment(
        self,
        *,
        run: CandidatePoolRun,
        policy: SellerTargetingPolicy,
        request: LongInactivityEnrichmentRequest,
        evaluations: list[tuple[CandidateFactBundle, TargetingEvaluation]],
    ) -> tuple[
        list[tuple[CandidateFactBundle, TargetingEvaluation]],
        LongInactivityExecutionResult,
    ]:
        """Run the opt-in D1A.1 path and retain full-policy evidence per candidate.

        The materializer is the upper Candidate planner, so it establishes the
        pre-existing fully eligible count from the complete policy.  The
        lower-level planner receives only deterministic activity state and can
        never promote a raw Long Inactivity match to an assignable candidate.
        """

        base_policy = policy.model_copy(update={"long_inactivity": None})
        updated: dict[UUID, TargetingEvaluation] = {}
        candidates: list[LongInactivityExecutionCandidate] = []
        already_fully_eligible = sum(
            evaluation.result is TargetingEvaluationResult.MATCH
            for _facts, evaluation in evaluations
        )
        for order, (facts, evaluation) in enumerate(evaluations):
            local_result, source = self._long_inactivity_result_and_source(evaluation)
            base_result = self._evaluate_targeting(base_policy, facts, as_of=run.as_of).result
            candidates.append(
                LongInactivityExecutionCandidate(
                    platform_account_id=facts.platform_account_id,
                    order=order,
                    local_result=local_result,
                    evidence_source=source,
                    # Current provider capability is intentionally XHS-only.
                    # Unavailable platforms stay UNKNOWN without spending an
                    # execution allowance or manufacturing a provider route.
                    eligible_if_activity_resolved=(
                        local_result is TargetingEvaluationResult.UNKNOWN
                        and base_result is TargetingEvaluationResult.MATCH
                        and facts.platform is Platform.XIAOHONGSHU
                    ),
                )
            )

        effective_budget = min(
            request.max_provider_enrichment,
            (
                self.long_inactivity_provider_budget
                if self.long_inactivity_provider_enricher is not None
                else 0
            ),
        )
        execution_request = LongInactivityExecutionRequest(
            planned_assignable_target=request.planned_assignable_target,
            already_fully_eligible=already_fully_eligible,
            max_provider_enrichment=effective_budget,
        )
        facts_by_account = {facts.platform_account_id: facts for facts, _evaluation in evaluations}

        async def enrich_one(
            candidate: LongInactivityExecutionCandidate,
        ) -> ProviderEnrichmentOutcome:
            provider_enricher = self.long_inactivity_provider_enricher
            if provider_enricher is None:
                return ProviderEnrichmentOutcome(
                    long_inactivity_result=TargetingEvaluationResult.UNKNOWN,
                    full_policy_result=TargetingEvaluationResult.UNKNOWN,
                )
            try:
                refresh = await provider_enricher(candidate.platform_account_id)
            except Exception:
                # Provider transport/governance failures are never candidate
                # matches. Content Activity persists its own secret-free
                # diagnostics and lease state; targeting stays fail-closed.
                refresh = LongInactivityProviderRefresh(
                    content_activity=None,
                    accepted_trusted_observation=False,
                )
            if not refresh.accepted_trusted_observation or refresh.content_activity is None:
                return ProviderEnrichmentOutcome(
                    long_inactivity_result=TargetingEvaluationResult.UNKNOWN,
                    full_policy_result=TargetingEvaluationResult.UNKNOWN,
                )
            refreshed_activity = refresh.content_activity
            refreshed_facts = facts_by_account[candidate.platform_account_id].model_copy(
                update={"content_activity": refreshed_activity}
            )
            refreshed_evaluation = self._evaluate_targeting(
                policy,
                refreshed_facts,
                # Candidate Run ``as_of`` is the one durable authoritative
                # execution clock. Never make preserved (or new) evidence
                # fresh by evaluating it at its own observation timestamp.
                as_of=_execution_as_of(run.as_of),
            )
            updated[candidate.platform_account_id] = refreshed_evaluation
            long_result, _source = self._long_inactivity_result_and_source(refreshed_evaluation)
            return ProviderEnrichmentOutcome(
                long_inactivity_result=long_result,
                full_policy_result=refreshed_evaluation.result,
            )

        result = await execute_bounded_long_inactivity_enrichment(
            execution_request,
            candidates,
            enrich_one=enrich_one,
        )
        return (
            [
                (facts, updated.get(facts.platform_account_id, evaluation))
                for facts, evaluation in evaluations
            ],
            result,
        )

    @staticmethod
    def _long_inactivity_execution_result_document(
        result: LongInactivityExecutionResult,
    ) -> dict[str, Any]:
        metrics = result.metrics
        return {
            "fully_eligible_count": result.fully_eligible_count,
            "candidates_evaluated": metrics.candidates_evaluated,
            "resolved_from_content_activity_cache": metrics.resolved_from_content_activity_cache,
            "resolved_from_grey_dolphin": metrics.resolved_from_grey_dolphin,
            "remained_unknown": metrics.remained_unknown,
            "provider_calls_attempted": metrics.provider_calls_attempted,
            "provider_calls_avoided_by_grey_dolphin": (
                metrics.provider_calls_avoided_by_grey_dolphin
            ),
            "target_reached": metrics.stopped_by_planned_target,
            "budget_reached": metrics.stopped_by_provider_budget,
        }

    async def materialize_run(self, run_id: UUID) -> CandidatePoolRun | None:
        """Claim and materialize one durable run in a single replay-safe transaction."""

        run = await self.repository.get_run_any(run_id, for_update=True)
        if run is None:
            await self.session.rollback()
            return None
        if run.status is not CandidatePoolRunStatus.PENDING:
            await self.session.commit()
            return run
        pool = await self.repository.get_pool(run.pool_id, department_id=None)
        if pool is None:
            await self.session.rollback()
            return None
        policy_record = await self.repository.get_policy(run.policy_id, pool_id=pool.id)
        if policy_record is None:
            return await self._mark_run_failed(
                run=run,
                pool=pool,
                error_code="TARGETING_POLICY_MISSING",
                error_message="Targeting policy is unavailable",
            )
        try:
            policy = self._typed_policy(pool, policy_record)
        except TargetingError:
            return await self._mark_run_failed(
                run=run,
                pool=pool,
                error_code="TARGETING_POLICY_INVALID",
                error_message="Targeting policy is invalid",
            )
        if pool.status is not CandidatePoolStatus.ACTIVE:
            return await self._mark_run_failed(
                run=run,
                pool=pool,
                error_code="CANDIDATE_POOL_INACTIVE",
                error_message="Candidate Pool is not active",
            )

        # Keep the FOR UPDATE lock through the final commit. A hard worker loss
        # then rolls this status and every staged member back to PENDING, which
        # lets broker redelivery or the pending-run reconciler safely retry.
        run.status = CandidatePoolRunStatus.RUNNING
        await self.session.flush()
        collection_context = self.repository.collection_context_snapshot(
            pool=pool,
            policy=policy,
            input_watermark=run.input_watermark,
        )
        try:
            enrichment_request = self._long_inactivity_enrichment_request(run)
            if enrichment_request is not None and not isinstance(policy, SellerTargetingPolicy):
                raise TargetingError(
                    409,
                    "LONG_INACTIVITY_ENRICHMENT_INVALID",
                    "Long Inactivity enrichment requires a Seller Long Inactivity policy",
                )
            if (
                enrichment_request is not None
                and isinstance(policy, SellerTargetingPolicy)
                and policy.long_inactivity is None
            ):
                raise TargetingError(
                    409,
                    "LONG_INACTIVITY_ENRICHMENT_INVALID",
                    "Long Inactivity enrichment requires a Seller Long Inactivity policy",
                )
            match_count = 0
            unknown_count = 0
            not_match_count = 0
            if enrichment_request is None:
                async for facts in self.repository.iter_candidate_fact_batches(
                    pool=pool,
                    policy=policy,
                    as_of=run.as_of,
                    freshness_policy=self.freshness_policy,
                    collection_context=collection_context,
                ):
                    increments = self.repository.add_evaluation_batch(
                        run=run,
                        evaluations=(
                            self._evaluation_record(policy, fact, as_of=run.as_of) for fact in facts
                        ),
                    )
                    match_count += increments[0]
                    unknown_count += increments[1]
                    not_match_count += increments[2]
                    await self.session.flush()
            else:
                # Provider work occurs only for this explicit run option. We
                # first resolve the complete Candidate policy cheaply for the
                # authoritative order, then hand only unresolved XHS activity
                # candidates to the bounded planner.
                initial_evaluations: list[tuple[CandidateFactBundle, TargetingEvaluation]] = []
                async for facts in self.repository.iter_candidate_fact_batches(
                    pool=pool,
                    policy=policy,
                    as_of=run.as_of,
                    freshness_policy=self.freshness_policy,
                    collection_context=collection_context,
                ):
                    initial_evaluations.extend(
                        (fact, self._evaluate_targeting(policy, fact, as_of=run.as_of))
                        for fact in facts
                    )
                assert isinstance(policy, SellerTargetingPolicy)
                evaluations, enrichment_result = await self._execute_long_inactivity_enrichment(
                    run=run,
                    policy=policy,
                    request=enrichment_request,
                    evaluations=initial_evaluations,
                )
                increments = self.repository.add_evaluation_batch(
                    run=run,
                    evaluations=((fact, evaluation, None) for fact, evaluation in evaluations),
                )
                match_count += increments[0]
                unknown_count += increments[1]
                not_match_count += increments[2]
                await self.session.flush()
                enrichment_watermark = dict(run.input_watermark or {})
                raw_execution = enrichment_watermark.get("long_inactivity_enrichment")
                if isinstance(raw_execution, dict):
                    raw_execution["result"] = self._long_inactivity_execution_result_document(
                        enrichment_result
                    )
                    enrichment_watermark["long_inactivity_enrichment"] = raw_execution
                    run.input_watermark = canonical_value(enrichment_watermark)
            run.match_count = match_count
            run.unknown_count = unknown_count
            run.not_match_count = not_match_count
            run.status = CandidatePoolRunStatus.COMPLETED
            run.error_code = None
            run.error_message = None
            watermark: dict[str, Any] = dict(run.input_watermark or {})
            watermark["materialized_at"] = _utc_now()
            watermark["materialized_member_count"] = (
                match_count
                + unknown_count
                + (not_match_count if isinstance(policy, BuyerTargetingPolicy) else 0)
            )
            run.input_watermark = canonical_value(watermark)
            self._add_run_audit(
                action=AuditAction.CANDIDATE_POOL_RUN_COMPLETED,
                result=AuditResult.SUCCESS,
                run=run,
                department_id=pool.department_id,
                ip="worker",
                user_agent="celery:materialize_candidate_pool_run",
                after=self._run_terminal_audit_after(run),
            )
            await self.session.commit()
            return run
        except TargetingError as error:
            return await self._mark_run_failed(
                run=run,
                pool=pool,
                error_code=error.code,
                error_message=error.message,
            )
        except BaseException:
            await self.session.rollback()
            failed_run = await self.repository.get_run_any(run_id, for_update=True)
            if failed_run is not None and failed_run.status is CandidatePoolRunStatus.PENDING:
                failed_pool = await self.repository.get_pool(failed_run.pool_id, department_id=None)
                if failed_pool is not None:
                    await self._mark_run_failed(
                        run=failed_run,
                        pool=failed_pool,
                        error_code="TARGETING_MATERIALIZATION_FAILED",
                        error_message="Candidate Pool materialization failed",
                    )
                else:
                    await self.session.rollback()
            else:
                await self.session.rollback()
            raise


__all__ = ["CandidatePoolService", "TargetingError"]
