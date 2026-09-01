import { ApiClientError, apiRequest } from "@/lib/api/client";

import type {
  CandidateCampaignAddResult,
  CandidateCampaignSelection,
  CandidateMemberPage,
  CandidateMemberPageSize,
  CandidatePool,
  CandidatePoolCreateRequest,
  CandidatePoolPage,
  CandidatePoolRun,
  CandidatePoolRunRequest,
  CandidatePoolRunPage,
  BuyerLeadTier,
  TargetingPolicy,
} from "./types";

export const CANDIDATE_POOL_PAGE_LIMIT = 50;
export const CANDIDATE_MEMBER_PAGE_LIMIT: CandidateMemberPageSize = 50;
export const CANDIDATE_MEMBER_PAGE_SIZES = [20, 50, 100, 200] as const;

function requireData<T>(data: T | null, label: string): T {
  if (data === null)
    throw new ApiClientError(`${label}响应缺少数据`, 200, "INVALID_RESPONSE");
  return data;
}

function encoded(value: string) {
  return encodeURIComponent(value);
}
function pagePath(
  path: string,
  cursor?: string | null,
  result?: "MATCH" | "UNKNOWN",
  limit = CANDIDATE_POOL_PAGE_LIMIT,
  buyerLeadTier?: BuyerLeadTier,
) {
  const params = new URLSearchParams({
    limit: String(limit),
  });
  if (cursor) params.set("cursor", cursor);
  if (result) params.set("result", result);
  if (buyerLeadTier) params.set("buyer_lead_tier", buyerLeadTier);
  return `${path}?${params.toString()}`;
}

export function candidatePoolPath(poolId: string) {
  return `/candidate-pools/${encoded(poolId)}`;
}
export function candidateRunPath(poolId: string, runId: string) {
  return `${candidatePoolPath(poolId)}/runs/${encoded(runId)}`;
}
export function candidateMemberPagePath(
  poolId: string,
  runId: string,
  cursor?: string | null,
  result?: "MATCH" | "UNKNOWN",
  pageSize: CandidateMemberPageSize = CANDIDATE_MEMBER_PAGE_LIMIT,
  buyerLeadTier?: BuyerLeadTier,
) {
  return pagePath(
    `${candidateRunPath(poolId, runId)}/members`,
    cursor,
    result,
    pageSize,
    buyerLeadTier,
  );
}

export async function fetchCandidatePoolPage(
  cursor?: string | null,
): Promise<CandidatePoolPage> {
  return requireData(
    (await apiRequest<CandidatePoolPage>(pagePath("/candidate-pools", cursor)))
      .data,
    "候选池列表",
  );
}
export async function createCandidatePool(
  payload: CandidatePoolCreateRequest,
  idempotencyKey: string,
): Promise<CandidatePool> {
  return requireData(
    (
      await apiRequest<CandidatePool>("/candidate-pools", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify(payload),
      })
    ).data,
    "创建候选池",
  );
}
export async function fetchCandidatePool(
  poolId: string,
): Promise<CandidatePool> {
  return requireData(
    (await apiRequest<CandidatePool>(candidatePoolPath(poolId))).data,
    "候选池",
  );
}
export async function fetchCandidatePolicies(
  poolId: string,
): Promise<TargetingPolicy[]> {
  return requireData(
    (
      await apiRequest<TargetingPolicy[]>(
        `${candidatePoolPath(poolId)}/policies`,
      )
    ).data,
    "规则历史",
  );
}
export async function fetchCandidateRunPage(
  poolId: string,
  cursor?: string | null,
): Promise<CandidatePoolRunPage> {
  return requireData(
    (
      await apiRequest<CandidatePoolRunPage>(
        pagePath(`${candidatePoolPath(poolId)}/runs`, cursor),
      )
    ).data,
    "生成记录",
  );
}
export async function fetchCandidateRun(
  poolId: string,
  runId: string,
): Promise<CandidatePoolRun> {
  return requireData(
    (await apiRequest<CandidatePoolRun>(candidateRunPath(poolId, runId))).data,
    "候选结果",
  );
}
export async function fetchCandidateMembers(
  poolId: string,
  runId: string,
  cursor?: string | null,
  result?: "MATCH" | "UNKNOWN",
  pageSize: CandidateMemberPageSize = CANDIDATE_MEMBER_PAGE_LIMIT,
  buyerLeadTier?: BuyerLeadTier,
): Promise<CandidateMemberPage> {
  return requireData(
    (
      await apiRequest<CandidateMemberPage>(
        candidateMemberPagePath(
          poolId,
          runId,
          cursor,
          result,
          pageSize,
          buyerLeadTier,
        ),
      )
    ).data,
    "候选结果列表",
  );
}
export async function createCandidateRun(
  poolId: string,
  idempotencyKey: string,
  payload: CandidatePoolRunRequest = {},
): Promise<CandidatePoolRun> {
  return requireData(
    (
      await apiRequest<CandidatePoolRun>(`${candidatePoolPath(poolId)}/runs`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify(payload),
      })
    ).data,
    "生成候选结果",
  );
}
export function addCandidatesToCampaign(
  campaignId: string,
  selection: CandidateCampaignSelection,
  idempotencyKey: string,
): Promise<CandidateCampaignAddResult>;
/** @deprecated Pass a CandidateCampaignSelection instead. */
export function addCandidatesToCampaign(
  campaignId: string,
  runId: string,
  memberIds: string[],
  idempotencyKey: string,
): Promise<CandidateCampaignAddResult>;
export async function addCandidatesToCampaign(
  campaignId: string,
  selectionOrRunId: CandidateCampaignSelection | string,
  idempotencyKeyOrMemberIds: string | string[],
  legacyIdempotencyKey?: string,
): Promise<CandidateCampaignAddResult> {
  const [selection, idempotencyKey] =
    typeof selectionOrRunId === "string"
      ? [
          {
            run_id: selectionOrRunId,
            member_ids: idempotencyKeyOrMemberIds as string[],
          } satisfies CandidateCampaignSelection,
          legacyIdempotencyKey as string,
        ]
      : [selectionOrRunId, idempotencyKeyOrMemberIds as string];
  return requireData(
    (
      await apiRequest<CandidateCampaignAddResult>(
        `/campaigns/${encoded(campaignId)}/members/from-candidate-run`,
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body: JSON.stringify(selection),
        },
      )
    ).data,
    "加入拓客活动",
  );
}
