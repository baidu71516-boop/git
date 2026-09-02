import { ApiClientError, apiRequest } from "@/lib/api/client";

import type {
  BuyerProspectRule,
  BuyerProspectRuleCreateRequest,
  BuyerProspectRuleLifecycleRequest,
  BuyerProspectRuleOptions,
  BuyerProspectRulePage,
  BuyerProspectRuleUpdateRequest,
} from "./types";
import type { CandidatePoolRun } from "@/features/candidate-pools/types";

export const BUYER_PROSPECT_RULE_PAGE_LIMIT = 50;

function requireData<T>(data: T | null, label: string): T {
  if (data === null) {
    throw new ApiClientError(`${label}响应缺少数据`, 200, "INVALID_RESPONSE");
  }
  return data;
}

function encoded(value: string) {
  return encodeURIComponent(value);
}

export function buyerProspectRulePath(ruleId: string) {
  return `/buyer-prospects/${encoded(ruleId)}`;
}

export function buyerProspectRunPath(ruleId: string, runId: string) {
  return `${buyerProspectRulePath(ruleId)}/runs/${encoded(runId)}`;
}

export async function fetchBuyerProspectRulePage(
  cursor?: string | null,
  includeArchived = false,
): Promise<BuyerProspectRulePage> {
  const params = new URLSearchParams({
    limit: String(BUYER_PROSPECT_RULE_PAGE_LIMIT),
  });
  if (cursor) params.set("cursor", cursor);
  if (includeArchived) params.set("include_archived", "true");
  return requireData(
    (await apiRequest<BuyerProspectRulePage>(
      `/buyer-prospects?${params.toString()}`,
    )).data,
    "潜在客户规则列表",
  );
}

export async function fetchBuyerProspectRuleOptions(): Promise<BuyerProspectRuleOptions> {
  return requireData(
    (await apiRequest<BuyerProspectRuleOptions>("/buyer-prospects/options"))
      .data,
    "潜在客户规则选项",
  );
}

export async function fetchBuyerProspectRule(
  ruleId: string,
): Promise<BuyerProspectRule> {
  return requireData(
    (await apiRequest<BuyerProspectRule>(buyerProspectRulePath(ruleId))).data,
    "潜在客户规则",
  );
}

export async function createBuyerProspectRule(
  payload: BuyerProspectRuleCreateRequest,
  idempotencyKey: string,
): Promise<BuyerProspectRule> {
  return requireData(
    (
      await apiRequest<BuyerProspectRule>("/buyer-prospects", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify(payload),
      })
    ).data,
    "创建潜在客户规则",
  );
}

export async function updateBuyerProspectRule(
  ruleId: string,
  payload: BuyerProspectRuleUpdateRequest,
): Promise<BuyerProspectRule> {
  return requireData(
    (
      await apiRequest<BuyerProspectRule>(buyerProspectRulePath(ruleId), {
        method: "PUT",
        body: JSON.stringify(payload),
      })
    ).data,
    "更新潜在客户规则",
  );
}

export async function setBuyerProspectRuleLifecycle(
  ruleId: string,
  payload: BuyerProspectRuleLifecycleRequest,
): Promise<BuyerProspectRule> {
  return requireData(
    (
      await apiRequest<BuyerProspectRule>(
        `${buyerProspectRulePath(ruleId)}/lifecycle`,
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      )
    ).data,
    "更新潜在客户规则状态",
  );
}

export async function runBuyerProspectRule(
  ruleId: string,
  idempotencyKey: string,
): Promise<CandidatePoolRun> {
  return requireData(
    (
      await apiRequest<CandidatePoolRun>(
        `${buyerProspectRulePath(ruleId)}/runs`,
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body: "{}",
        },
      )
    ).data,
    "生成潜在客户名单",
  );
}
