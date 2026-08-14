import type {
  RefreshFreshnessStatus,
  RefreshPriorityReason,
  RefreshQueue,
  RefreshQueueDetail,
  RefreshQueueItem,
  RefreshQueueItemStatus,
  RefreshQueueStatus,
} from "./types";

const queueStatuses: RefreshQueueStatus[] = [
  "open",
  "exported",
  "completed",
  "cancelled",
];

export const refreshQueuePreviewQueues: RefreshQueue[] = queueStatuses.map(
  (status, index) => ({
    id: status,
    department_id: "00000000-0000-0000-0000-000000000101",
    created_by_operator_id: "00000000-0000-0000-0000-000000000201",
    status,
    as_of: `2026-08-${String(14 - index).padStart(2, "0")}T01:00:00Z`,
    requested_limit: 120 - index * 10,
    today_total_limit: 180,
    refresh_limit: 140,
    policy_version: 1,
    criteria_snapshot: {},
    created_at: `2026-08-${String(14 - index).padStart(2, "0")}T01:02:00Z`,
    updated_at: `2026-08-${String(14 - index).padStart(2, "0")}T02:00:00Z`,
    exported_at: status === "open" ? null : "2026-08-14T02:00:00Z",
    completed_at: status === "completed" ? "2026-08-14T04:00:00Z" : null,
    cancelled_at: status === "cancelled" ? "2026-08-14T04:00:00Z" : null,
  }),
);

const freshnessSequence: Array<{
  status: RefreshFreshnessStatus;
  tier: 1 | 2 | 3 | 4 | 5;
  reason: RefreshPriorityReason;
}> = [
  { status: "unknown", tier: 1, reason: "FRESHNESS_UNKNOWN" },
  { status: "very_stale", tier: 2, reason: "VERY_STALE" },
  { status: "stale", tier: 3, reason: "STALE" },
  { status: "aging", tier: 4, reason: "AGING" },
  { status: "fresh", tier: 5, reason: "FOLLOWERS_MISSING" },
];

const returnStatuses: RefreshQueueItemStatus[] = [
  "pending",
  "fulfilled_changed",
  "fulfilled_no_change",
  "stale_return",
  "unresolved",
  "cancelled",
];

export function createRefreshQueuePreviewItems(
  queueStatus: RefreshQueueStatus,
): RefreshQueueItem[] {
  return Array.from({ length: 55 }, (_, index) => {
    const freshness = freshnessSequence[index % freshnessSequence.length];
    const itemStatus =
      queueStatus === "completed"
        ? index % 2 === 0
          ? "fulfilled_changed"
          : "fulfilled_no_change"
        : queueStatus === "cancelled"
          ? "cancelled"
          : returnStatuses[index % returnStatuses.length];
    const suffix = String(index + 1).padStart(3, "0");
    const includeSecondaryReason = freshness.tier < 5 && index % 3 === 0;
    return {
      id: `00000000-0000-0000-0000-000000001${suffix}`,
      department_id: "00000000-0000-0000-0000-000000000101",
      queue_id: queueStatus,
      influencer_id: `00000000-0000-0000-0000-000000002${suffix}`,
      platform_account_id: `00000000-0000-0000-0000-000000003${suffix}`,
      source: "huitun",
      priority_tier: freshness.tier,
      priority_reasons: includeSecondaryReason
        ? [freshness.reason, "FOLLOWERS_MISSING"]
        : [freshness.reason],
      identity_snapshot: {
        schema_version: 1,
        platform: "xiaohongshu",
        account_name:
          index === 0 ? "山野生活研究所" : `预览达人账号 ${index + 1}`,
        platform_account_id: `preview-account-${suffix}`,
        account_handle: index % 4 === 0 ? `preview_handle_${suffix}` : null,
        profile_url: null,
        external_source_id: null,
        followers_count: index % 5 === 4 ? null : 12_000 + index * 381,
      },
      baseline_last_observed_at:
        freshness.status === "unknown"
          ? null
          : `2026-0${(index % 7) + 1}-01T00:00:00Z`,
      baseline_source_updated_at: null,
      status: itemStatus,
      fulfilled_import_job_id: null,
      fulfilled_import_row_id: null,
      fulfilled_at: null,
      last_return_import_job_id: null,
      last_return_import_row_id: null,
      created_at: "2026-08-14T01:02:00Z",
      updated_at: "2026-08-14T01:02:00Z",
    };
  });
}

function countBy<T extends string>(values: T[]): Partial<Record<T, number>> {
  return values.reduce<Partial<Record<T, number>>>((counts, value) => {
    counts[value] = (counts[value] ?? 0) + 1;
    return counts;
  }, {});
}

export function getRefreshQueuePreviewDetail(
  status: RefreshQueueStatus,
): RefreshQueueDetail {
  const queue =
    refreshQueuePreviewQueues.find(
      (candidate) => candidate.status === status,
    ) ?? refreshQueuePreviewQueues[0];
  const items = createRefreshQueuePreviewItems(status);
  return {
    queue,
    summary: {
      requested: queue.requested_limit,
      selected: items.length,
      unique_influencers: items.length,
      freshness_breakdown: countBy(
        items.map((item) => freshnessSequence[item.priority_tier - 1].status),
      ),
      priority_breakdown: countBy(
        items.map(
          (item) => String(item.priority_tier) as "1" | "2" | "3" | "4" | "5",
        ),
      ),
      status_breakdown: countBy(items.map((item) => item.status)),
    },
  };
}
