import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BulkImportWorkspace } from "@/features/imports/bulk-import-workspace";
import { AMBIGUOUS_BULK_CONFIRM_MESSAGE } from "@/features/imports/formatters";
import type {
  CollectionJobPublic,
  ImportDispatchResult,
  ImportJobFilePublic,
  ImportJobPublic,
  ImportJobStatus,
  ImportRowAction,
  ImportRowPublic,
  ImportRowsPage,
  ScreeningResult,
  UnifiedPreviewSummary,
} from "@/features/imports/types";
import type { RefreshQueueDetail } from "@/features/refresh-queues/types";

const bulkPreviewPresentationMode = vi.hoisted(() => ({ compact: false }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock(
  "@/features/imports/components/bulk-preview-table",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("@/features/imports/components/bulk-preview-table")
      >();
    return {
      ...actual,
      BulkPreviewTable: (props: { rows: unknown[] }) =>
        bulkPreviewPresentationMode.compact ? (
          <section aria-label="数据预览表格">{props.rows.length}</section>
        ) : (
          actual.BulkPreviewTable(props as never)
        ),
    };
  },
);

vi.mock(
  "@/features/imports/components/bulk-preview-summary",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("@/features/imports/components/bulk-preview-summary")
      >();
    return {
      ...actual,
      BulkPreviewSummary: (props: unknown) =>
        bulkPreviewPresentationMode.compact ? (
          <section aria-label="数据预览摘要" />
        ) : (
          actual.BulkPreviewSummary(props as never)
        ),
    };
  },
);

const collection: CollectionJobPublic = {
  id: "collection-1",
  name: "美妆达人采集",
  industry: "美妆",
  subdirection: "护肤",
  purpose: "商务开发",
  target_action: "邮件触达",
  follower_min: 10_000,
  follower_max: 1_000_000,
  target_count: 75,
  department_id: "department-1",
  owner_operator_id: "operator-1",
  source_type: "manual_huitun_export",
  status: "active",
  notes: null,
  screening_rules: {
    schema_version: 1,
    platforms: ["xiaohongshu"],
    source_tags_exact_any: ["美妆"],
  },
  screening_rules_revision: 4,
  created_at: "2026-08-10T00:00:00Z",
  updated_at: "2026-08-13T00:00:00Z",
};

function makeFile(
  overrides: Partial<ImportJobFilePublic> = {},
): ImportJobFilePublic {
  return {
    id: "file-1",
    import_job_id: "job-1",
    stored_file_id: "stored-1",
    position: 1,
    original_filename: "灰豚_美妆达人.csv",
    declared_mime: "text/csv",
    status: "ready",
    source_acquired_at: "2026-08-12T10:00:00+08:00",
    source_acquired_at_origin: "user_confirmed",
    source_acquired_at_confirmation_required: false,
    detected_type: "csv",
    detected_mime: "text/csv",
    file_size: 4_096,
    sha256: "a".repeat(64),
    detected_fields: ["达人名称", "小红书号", "粉丝数"],
    field_mapping: {
      达人名称: "nickname",
      小红书号: "account_handle",
      粉丝数: "followers_count",
    },
    mapping_hash: "b".repeat(64),
    raw_rows: 75,
    warning_rows: 3,
    error_rows: 1,
    error_code: null,
    error_message: null,
    parse_task_id: "parse-task-1",
    parse_attempts: 1,
    parse_started_at: "2026-08-12T02:00:00Z",
    parse_completed_at: "2026-08-12T02:00:01Z",
    excluded_at: null,
    created_at: "2026-08-12T02:00:00Z",
    updated_at: "2026-08-12T02:00:01Z",
    ...overrides,
  };
}

const file = makeFile();

function makeSummary(
  overrides: Partial<UnifiedPreviewSummary> = {},
): UnifiedPreviewSummary {
  return {
    schema_version: 1,
    context_hash: "c".repeat(64),
    batch_plan_hash: "d".repeat(64),
    manifest: [
      {
        import_job_file_id: file.id,
        position: 1,
        stored_file_sha256: file.sha256,
        mapping_hash: file.mapping_hash,
        status: "ready",
        included: true,
        source_acquired_at: file.source_acquired_at,
        source_acquired_at_origin: "user_confirmed",
        source_acquired_at_confirmation_required: false,
      },
    ],
    screening_rule_snapshot: {
      rules: collection.screening_rules,
      rule_revision: collection.screening_rules_revision,
      follower_min: collection.follower_min,
      follower_max: collection.follower_max,
      rule_hash: "e".repeat(64),
    },
    file_count: 1,
    occurrence_count: 1,
    excluded_file_count: 0,
    raw_rows: 75,
    unique_rows: 73,
    internal_duplicate_rows: 2,
    existing_rows: 50,
    new_rows: 20,
    changed_rows: 10,
    no_change_rows: 40,
    created_rows: 20,
    updated_rows: 10,
    skipped_rows: 2,
    error_rows: 1,
    manual_review_rows: 2,
    warning_rows: 3,
    possible_duplicate_contact_rows: 1,
    screened_rows: 72,
    screening_match_rows: 30,
    screening_not_match_rows: 30,
    screening_unknown_rows: 12,
    ...overrides,
  };
}

const summary = makeSummary();

const refreshReturnSummary = {
  schema_version: 1 as const,
  row_count: 75,
  queue_item_count: 6,
  matched_row_count: 70,
  outside_queue_row_count: 2,
  pending_row_count: 3,
  queue_items_with_return_count: 5,
  queue_items_without_return_count: 1,
  expected_fulfilled_changed_count: 1,
  expected_fulfilled_no_change_count: 1,
  expected_stale_return_count: 1,
  expected_unresolved_count: 1,
  unchanged_terminal_item_count: 1,
  conflict_item_count: 1,
  missing_queue_item_ids: ["queue-item-missing"],
};

function makeRefreshQueueDetail(
  status: "exported" | "completed" = "exported",
): RefreshQueueDetail {
  return {
    queue: {
      id: "queue-1",
      department_id: "department-1",
      created_by_operator_id: "operator-1",
      status,
      as_of: "2026-08-01T00:00:00Z",
      requested_limit: 6,
      today_total_limit: 10,
      refresh_limit: 6,
      policy_version: 1,
      criteria_snapshot: {},
      created_at: "2026-08-14T00:00:00Z",
      updated_at: "2026-08-14T03:00:00Z",
      exported_at: "2026-08-14T01:00:00Z",
      completed_at: status === "completed" ? "2026-08-14T03:00:00Z" : null,
      cancelled_at: null,
    },
    summary: {
      requested: 6,
      selected: 6,
      unique_influencers: 6,
      freshness_breakdown: { stale: 6 },
      priority_breakdown: { "3": 6 },
      status_breakdown:
        status === "completed"
          ? { fulfilled_changed: 3, fulfilled_no_change: 3 }
          : {
              fulfilled_changed: 1,
              fulfilled_no_change: 1,
              stale_return: 1,
              unresolved: 1,
              pending: 2,
            },
    },
  };
}

function makeJob(overrides: Partial<ImportJobPublic> = {}): ImportJobPublic {
  return {
    id: "job-1",
    collection_job_id: collection.id,
    refresh_queue_id: null,
    department_id: "department-1",
    operator_id: "operator-1",
    stored_file_id: null,
    original_filename: null,
    mime_type: null,
    file_size: null,
    sha256: null,
    source_type: "manual_huitun_export",
    status: "preview_ready",
    detected_fields: null,
    field_mapping: null,
    mapping_hash: null,
    preview_revision: 4,
    preview_summary: summary,
    result: null,
    total_rows: 75,
    valid_rows: 74,
    warning_rows: 3,
    error_rows: 1,
    created_rows: 20,
    updated_rows: 10,
    no_change_rows: 40,
    skipped_rows: 2,
    manual_review_rows: 2,
    confirmed_revision: null,
    confirmed_at: null,
    completed_at: null,
    error_code: null,
    error_message: null,
    created_at: "2026-08-12T00:00:00Z",
    updated_at: "2026-08-13T00:00:00Z",
    ...overrides,
  };
}

function screening(result: ScreeningResult) {
  return {
    result,
    rule_schema_version: 1 as const,
    rule_revision: 4,
    rule_hash: "e".repeat(64),
    evidence: [
      {
        rule: "platforms" as const,
        result: "MATCH" as const,
        configured: ["xiaohongshu"],
        observed: "xiaohongshu",
        reason: "PLATFORM_MATCH",
      },
      {
        rule: "source_tags_exact_any" as const,
        result,
        configured: ["美妆"],
        observed: result === "UNKNOWN" ? null : ["美妆"],
        reason:
          result === "MATCH"
            ? "SOURCE_TAG_MATCH"
            : result === "NOT_MATCH"
              ? "SOURCE_TAG_NOT_MATCH"
              : "SOURCE_TAGS_MISSING_OR_INVALID",
      },
    ],
  };
}

const emptyChangeSummary = {
  effective_changes: [],
  ignored_changes: [],
  freshness_changes: [],
  historical_observations: [],
};

function makeRow(overrides: Partial<ImportRowPublic> = {}): ImportRowPublic {
  return {
    id: "row-update",
    import_job_id: "job-1",
    import_job_file_id: file.id,
    row_number: 28,
    raw_data: { 达人名称: "白桃汽水" },
    normalized_data: {
      display_name: "白桃汽水",
      platform_identity: { platform: "xiaohongshu" },
    },
    matched_influencer_id: "influencer-1",
    matched_platform_account_id: "account-1",
    match_type: "platform_account_id",
    action: "update",
    merge_plan: {
      screening: screening("MATCH"),
      change_summary: {
        effective_changes: [
          {
            scope: "current_metrics",
            field: "followers_count",
            before: 182_000,
            incoming: 206_600,
            after: 206_600,
            effect: "apply",
            reason: "NEWER_METRICS",
            added: [],
            removed: [],
          },
        ],
        ignored_changes: [
          {
            scope: "account",
            field: "bio",
            before: "人工维护简介",
            incoming: "来源简介",
            after: "人工维护简介",
            effect: "ignore",
            reason: "MANUAL_VALUE_PROTECTED",
            added: [],
            removed: [],
          },
        ],
        freshness_changes: [
          {
            scope: "freshness",
            field: "source_acquired_at",
            before: "2026-08-01T10:00:00+08:00",
            incoming: "2026-08-12T10:00:00+08:00",
            after: "2026-08-12T10:00:00+08:00",
            effect: "observe",
            reason: "OBSERVATION_ADVANCED",
            added: [],
            removed: [],
          },
        ],
        historical_observations: [
          {
            scope: "metric_snapshot",
            field: "metrics",
            before: null,
            incoming: { followers_count: 206_600 },
            after: null,
            effect: "history",
            reason: "SNAPSHOT_ONLY",
            added: [],
            removed: [],
          },
        ],
      },
      preview_context_hash: "c".repeat(64),
    },
    warnings: [
      {
        code: "POSSIBLE_DUPLICATE_CONTACT",
        message: "raw private warning",
      },
    ],
    errors: [],
    preview_revision: 4,
    plan_hash: "f".repeat(64),
    committed_action: null,
    committed_at: null,
    ...overrides,
  };
}

function actionRow(
  id: string,
  displayName: string,
  action: ImportRowAction,
  result: ScreeningResult | null,
): ImportRowPublic {
  return makeRow({
    id,
    row_number: 30 + Number(id.slice(-1)),
    normalized_data: { display_name: displayName },
    matched_influencer_id: null,
    matched_platform_account_id: null,
    match_type: "none",
    action,
    merge_plan: {
      ...(result ? { screening: screening(result) } : {}),
      change_summary: emptyChangeSummary,
      preview_context_hash: "c".repeat(64),
    },
    warnings: [],
    errors:
      action === "error"
        ? [{ code: "MISSING_REQUIRED_FIELD", message: "raw error" }]
        : [],
  });
}

const initialRows: ImportRowPublic[] = [
  makeRow(),
  actionRow("row-2", "春日海盐", "create", "NOT_MATCH"),
  actionRow("row-3", "栗子拿铁", "no_change", "UNKNOWN"),
  actionRow("row-4", "重复账号", "skip", null),
  actionRow("row-5", "缺失身份", "error", null),
  actionRow("row-6", "身份冲突", "manual_review", "MATCH"),
];

function page(
  items: ImportRowPublic[] = initialRows,
  overrides: Partial<ImportRowsPage> = {},
): ImportRowsPage {
  return { items, total: 75, offset: 0, limit: 50, ...overrides };
}

function success(data: unknown, status = 200): Response {
  return new Response(
    JSON.stringify({
      success: true,
      data,
      error: null,
      request_id: "request-1",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function failure(
  code: string,
  status: number,
  message = "raw worker or broker detail",
): Response {
  return new Response(
    JSON.stringify({
      success: false,
      data: null,
      error: { code, message, details: null },
      request_id: "request-1",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function dispatch(
  status: ImportJobStatus,
  idempotent = false,
): ImportDispatchResult {
  return {
    import_job_id: "job-1",
    status,
    preview_revision: 4,
    task_id: "task-confirm-1",
    idempotent,
  };
}

type MutationRouter = (
  path: string,
  init: RequestInit | undefined,
) => Response | Promise<Response> | undefined;

function installBulkApi({
  getJob = () => makeJob(),
  getRows = () => page(),
  getRefreshQueue,
  mutation,
}: {
  getJob?: () => ImportJobPublic;
  getRows?: (params: URLSearchParams) => ImportRowsPage;
  getRefreshQueue?: () => RefreshQueueDetail;
  mutation?: MutationRouter;
}) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const rawUrl = String(input);
      const url = new URL(rawUrl, "http://local.test");
      const method = (init?.method ?? "GET").toUpperCase();
      if (method !== "GET") {
        const response = await mutation?.(url.pathname, init);
        if (response !== undefined) return response;
      }
      if (method === "GET" && url.pathname === "/api/v1/import-jobs/job-1") {
        return success(getJob());
      }
      if (
        method === "GET" &&
        url.pathname === "/api/v1/import-jobs/job-1/files"
      ) {
        return success([file]);
      }
      if (
        method === "GET" &&
        url.pathname === "/api/v1/collection-jobs/collection-1"
      ) {
        return success(collection);
      }
      if (
        method === "GET" &&
        url.pathname === "/api/v1/import-jobs/job-1/rows"
      ) {
        return success(getRows(url.searchParams));
      }
      if (
        method === "GET" &&
        url.pathname === "/api/v1/refresh-queues/queue-1" &&
        getRefreshQueue
      ) {
        return success(getRefreshQueue());
      }
      throw new Error(`Unexpected request: ${method} ${rawUrl}`);
    });
}

const queryClients: QueryClient[] = [];

function renderWithClient(node: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      // The product hooks must override this for every ambiguous mutation.
      mutations: { retry: 3, retryDelay: 0 },
    },
  });
  queryClients.push(queryClient);
  return render(
    <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>,
  );
}

function renderBulk(
  role: "operator" | "viewer" = "operator",
): ReturnType<typeof renderWithClient> {
  return renderWithClient(
    <BulkImportWorkspace
      role={role}
      jobId="job-1"
      onSelectJob={vi.fn()}
      onClearJob={vi.fn()}
    />,
  );
}

function previewTableRow(name: string): HTMLElement {
  const row = screen.getByText(name).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

function rowsRequests(fetchSpy: ReturnType<typeof installBulkApi>): URL[] {
  return fetchSpy.mock.calls
    .map(([input]) => new URL(String(input), "http://local.test"))
    .filter((url) => url.pathname.endsWith("/import-jobs/job-1/rows"));
}

function mutationRequests(
  fetchSpy: ReturnType<typeof installBulkApi>,
  suffix: string,
) {
  return fetchSpy.mock.calls.filter(
    ([input, init]) =>
      new URL(String(input), "http://local.test").pathname.endsWith(suffix) &&
      (init?.method ?? "GET") === "POST",
  );
}

beforeEach(() => {
  bulkPreviewPresentationMode.compact = false;
  document.cookie = "outreach_csrf=test-csrf; path=/";
});

afterEach(() => {
  cleanup();
  bulkPreviewPresentationMode.compact = false;
  for (const queryClient of queryClients.splice(0)) queryClient.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("Bulk unified preview review", () => {
  it("loads the real summary and category page, resets pagination, and presents the table and four change buckets in Chinese", async () => {
    const fetchSpy = installBulkApi({
      getRows: (params) => {
        const category = params.get("category");
        const offset = Number(params.get("offset"));
        if (category === "warning") {
          return page([], { total: 3, offset: 0 });
        }
        if (offset === 50) {
          return page([actionRow("row-7", "第二页达人", "create", "MATCH")], {
            offset: 50,
          });
        }
        return page();
      },
    });

    renderBulk();

    expect(await screen.findByText("白桃汽水")).toBeInTheDocument();
    expect(screen.getByText("核心结果")).toBeInTheDocument();
    expect(screen.queryByText("更新回流预览")).not.toBeInTheDocument();
    expect(screen.getAllByText("筛选结果").length).toBeGreaterThan(0);
    const firstRequest = rowsRequests(fetchSpy)[0];
    expect(firstRequest?.searchParams.toString()).toBe(
      "category=all&offset=0&limit=50",
    );

    const expectedRows = [
      ["白桃汽水", "更新", "符合条件"],
      ["春日海盐", "新增", "不符合条件"],
      ["栗子拿铁", "无需变更", "信息不足"],
      ["重复账号", "跳过", "—"],
      ["缺失身份", "错误", "—"],
      ["身份冲突", "需人工处理", "符合条件"],
    ] as const;
    for (const [name, action, result] of expectedRows) {
      const row = within(previewTableRow(name));
      expect(row.getByText(action)).toBeInTheDocument();
      if (result === "—") {
        expect(row.getAllByText(result).length).toBeGreaterThan(0);
      } else {
        expect(row.getByText(result)).toBeInTheDocument();
      }
    }
    expect(
      within(previewTableRow("白桃汽水")).getByText(/^1 项$/),
    ).toBeInTheDocument();

    const pagination = document.querySelector(".bulk-preview-pagination");
    expect(pagination).not.toBeNull();
    fireEvent.click(within(pagination as HTMLElement).getByText("2"));
    await waitFor(() => {
      const latest = rowsRequests(fetchSpy).at(-1);
      expect(latest?.searchParams.get("offset")).toBe("50");
      expect(latest?.searchParams.get("category")).toBe("all");
    });

    const categoryFilter = document.querySelector(
      ".bulk-preview-category-filter",
    );
    expect(categoryFilter).not.toBeNull();
    fireEvent.click(
      within(categoryFilter as HTMLElement).getByText("有警告", {
        exact: false,
      }),
    );
    await waitFor(() => {
      const latest = rowsRequests(fetchSpy).at(-1);
      expect(latest?.searchParams.get("category")).toBe("warning");
      expect(latest?.searchParams.get("offset")).toBe("0");
    });
    for (const request of rowsRequests(fetchSpy)) {
      expect([...request.searchParams.keys()]).toEqual([
        "category",
        "offset",
        "limit",
      ]);
      expect(request.searchParams.has("action")).toBe(false);
    }

    fireEvent.click(
      within(categoryFilter as HTMLElement).getByText("全部", {
        exact: false,
      }),
    );
    const updateRow = await screen.findByText("白桃汽水");
    fireEvent.click(
      within(updateRow.closest("tr") as HTMLElement).getByRole("button", {
        name: "查看",
      }),
    );

    const drawer = await screen.findByRole("dialog", {
      name: /白桃汽水/,
    });
    expect(within(drawer).getByText("变更摘要")).toBeInTheDocument();
    for (const bucket of [
      "本次会更新",
      "不会更新",
      "数据更新时间变化",
      "历史观察",
    ]) {
      expect(within(drawer).getByText(bucket)).toBeInTheDocument();
    }
    expect(within(drawer).getByText("粉丝数")).toBeInTheDocument();
    expect(within(drawer).getByText("简介")).toBeInTheDocument();
    expect(within(drawer).getByText("数据取得时间")).toBeInTheDocument();
    expect(within(drawer).getByText("指标快照")).toBeInTheDocument();
    expect(drawer.textContent).not.toContain("effective_changes");
    expect(drawer.textContent).not.toContain("raw private warning");
  });

  it("lets a Viewer read the same Preview rows but never presents Confirm", async () => {
    const fetchSpy = installBulkApi({});

    renderBulk("viewer");

    expect(await screen.findByText("白桃汽水")).toBeInTheDocument();
    expect(rowsRequests(fetchSpy)).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "确认导入" }),
    ).not.toBeInTheDocument();
    expect(mutationRequests(fetchSpy, "/confirm")).toHaveLength(0);
  });

  it("presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking", async () => {
    const staleRow = makeRow({
      merge_plan: {
        ...makeRow().merge_plan,
        refresh_return: {
          locator: {
            import_job_file_id: file.id,
            file_position: 1,
            row_number: 28,
            import_row_id: "row-update",
          },
          outcome: "expected_fulfillment",
          queue_item_id: "queue-item-1",
          expected_status: "stale_return",
          reason: "ACQUISITION_NOT_NEWER_THAN_BASELINE",
          is_last_return_claimant: true,
        },
      },
    });
    const unresolvedRow = actionRow(
      "row-6",
      "身份冲突",
      "manual_review",
      "MATCH",
    );
    unresolvedRow.merge_plan = {
      ...unresolvedRow.merge_plan,
      refresh_return: {
        locator: {
          import_job_file_id: file.id,
          file_position: 1,
          row_number: unresolvedRow.row_number,
          import_row_id: unresolvedRow.id,
        },
        outcome: "expected_fulfillment",
        queue_item_id: "queue-item-2",
        expected_status: "unresolved",
        reason: "MULTIPLE_OWNER_ROWS",
        is_last_return_claimant: true,
      },
    };
    installBulkApi({
      getJob: () =>
        makeJob({
          refresh_queue_id: "queue-1",
          preview_summary: makeSummary({
            refresh_return: refreshReturnSummary,
          }),
        }),
      getRows: () => page([staleRow, unresolvedRow]),
      getRefreshQueue: () => makeRefreshQueueDetail(),
    });

    renderBulk();

    expect(await screen.findByText("更新名单回流")).toBeInTheDocument();
    expect(await screen.findByText("更新回流预览")).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "回流预览" }),
    ).toBeInTheDocument();
    expect(
      within(previewTableRow("白桃汽水")).getByText("回流数据已过期"),
    ).toBeInTheDocument();
    expect(
      within(previewTableRow("身份冲突")).getByText("需要进一步确认"),
    ).toBeInTheDocument();

    fireEvent.click(
      within(previewTableRow("白桃汽水")).getByRole("button", {
        name: "查看",
      }),
    );
    const drawer = await screen.findByRole("dialog", { name: /白桃汽水/ });
    expect(within(drawer).getByText("更新回流")).toBeInTheDocument();
    expect(
      within(drawer).getByText("回流数据时间不晚于名单基准"),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByText(/本次数据可以继续参与正常导入/),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByText("是否为本次有效回流行"),
    ).toBeInTheDocument();

    fireEvent.click(
      within(drawer).getByRole("button", { name: "关闭预览数据详情" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    const modal = await screen.findByRole("dialog", {
      name: "确认导入这批数据？",
    });
    expect(within(modal).getByText("更新回流预览")).toBeInTheDocument();
    expect(
      within(modal).getByText(/不会阻止其他有效数据导入/),
    ).toBeInTheDocument();
    expect(
      within(modal).getByRole("button", { name: "确认导入" }),
    ).toBeEnabled();
  });

  it("shows final return counts only from the refetched Queue detail", async () => {
    installBulkApi({
      getJob: () =>
        makeJob({
          refresh_queue_id: "queue-1",
          status: "completed",
          completed_at: "2026-08-14T03:00:00Z",
          preview_summary: makeSummary({
            refresh_return: refreshReturnSummary,
          }),
          result: {
            import_job_id: "job-1",
            preview_revision: 4,
            created_rows: 20,
            updated_rows: 10,
            no_change_rows: 40,
            skipped_rows: 2,
            error_rows: 1,
            manual_review_rows: 2,
          },
        }),
      getRefreshQueue: () => makeRefreshQueueDetail("completed"),
    });

    renderBulk();

    expect(await screen.findByText("更新名单回流结果")).toBeInTheDocument();
    const result = screen.getByLabelText("更新名单回流结果");
    expect(within(result).getByText("已回流 · 有变更")).toBeInTheDocument();
    expect(within(result).getAllByText("3")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "查看更新名单" })).toHaveAttribute(
      "href",
      "/refresh-queues/queue-1",
    );
    expect(
      screen.getByRole("link", { name: "查看达人库" }),
    ).toBeInTheDocument();
  });
});

describe("Bulk Confirm acceptance and recovery", () => {
  it.each([
    [202, false],
    [200, true],
  ] as const)(
    "accepts HTTP %i dispatch data, closes the explicit Modal, and polls the Job without another Confirm",
    async (responseStatus, idempotent) => {
      bulkPreviewPresentationMode.compact = true;
      let accepted = false;
      let postConfirmJobReads = 0;
      const fetchSpy = installBulkApi({
        getJob: () => {
          if (!accepted) return makeJob();
          postConfirmJobReads += 1;
          return makeJob({
            status: postConfirmJobReads === 1 ? "confirm_queued" : "importing",
            confirmed_revision: 4,
            confirmed_at: "2026-08-13T10:00:00Z",
          });
        },
        mutation: (path) => {
          if (path.endsWith("/import-jobs/job-1/confirm")) {
            accepted = true;
            return success(
              dispatch("confirm_queued", idempotent),
              responseStatus,
            );
          }
          return undefined;
        },
      });

      renderBulk();
      const confirmButton = await screen.findByRole("button", {
        name: "确认导入",
      });
      expect(mutationRequests(fetchSpy, "/confirm")).toHaveLength(0);

      fireEvent.click(confirmButton);
      const modal = await screen.findByRole("dialog", {
        name: "确认导入这批数据？",
      });
      expect(within(modal).getByText("新增")).toBeInTheDocument();
      expect(within(modal).getByText("20")).toBeInTheDocument();
      fireEvent.click(within(modal).getByRole("button", { name: "确认导入" }));

      expect(await screen.findByText("正在准备导入")).toBeInTheDocument();
      const confirmCalls = mutationRequests(fetchSpy, "/confirm");
      expect(confirmCalls).toHaveLength(1);
      expect(JSON.parse(String(confirmCalls[0]?.[1]?.body))).toEqual({
        preview_revision: 4,
      });
      expect(
        Object.keys(JSON.parse(String(confirmCalls[0]?.[1]?.body))),
      ).toEqual(["preview_revision"]);
      expect(
        new Headers(confirmCalls[0]?.[1]?.headers).get("X-CSRF-Token"),
      ).toBe("test-csrf");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

      expect(
        await screen.findByText("正在导入", {}, { timeout: 2_500 }),
      ).toBeInTheDocument();
      expect(postConfirmJobReads).toBeGreaterThanOrEqual(2);
      expect(mutationRequests(fetchSpy, "/confirm")).toHaveLength(1);
      expect(document.body.textContent).not.toMatch(/\b\d+%\b/);
    },
  );

  it.each(["network", "server"] as const)(
    "does not retry an ambiguous %s result, freezes Confirm, and checks the Job with GET",
    async (failureKind) => {
      bulkPreviewPresentationMode.compact = true;
      let jobReads = 0;
      const fetchSpy = installBulkApi({
        getJob: () => {
          jobReads += 1;
          return makeJob();
        },
        mutation: (path) => {
          if (!path.endsWith("/import-jobs/job-1/confirm")) return undefined;
          if (failureKind === "network") {
            throw new TypeError("network connection lost");
          }
          return failure("INTERNAL_ERROR", 503, "broker stack trace");
        },
      });

      renderBulk();
      fireEvent.click(await screen.findByRole("button", { name: "确认导入" }));
      const modal = await screen.findByRole("dialog", {
        name: "确认导入这批数据？",
      });
      fireEvent.click(within(modal).getByRole("button", { name: "确认导入" }));

      expect(
        await screen.findByText(AMBIGUOUS_BULK_CONFIRM_MESSAGE),
      ).toBeInTheDocument();
      await waitFor(() => expect(jobReads).toBeGreaterThanOrEqual(2));
      expect(mutationRequests(fetchSpy, "/confirm")).toHaveLength(1);
      expect(screen.queryByRole("button", { name: "确认导入" })).toBeDisabled();
      expect(document.body.textContent).not.toContain("broker stack trace");
      expect(document.body.textContent).not.toContain(
        "network connection lost",
      );
    },
  );
});

describe("Bulk Preview and Confirm terminal states", () => {
  it("rebuilds a stale Preview with rebuild=true and never sends Confirm", async () => {
    const fetchSpy = installBulkApi({
      getJob: () =>
        makeJob({
          status: "preview_stale",
          confirmed_revision: 4,
          error_code: "PREVIEW_STALE",
        }),
      mutation: (path) =>
        path.endsWith("/import-jobs/job-1/preview")
          ? success(dispatch("previewing"), 202)
          : undefined,
    });

    renderBulk();
    const staleNotice = await screen.findByRole("alert");
    expect(staleNotice).toHaveTextContent("本次预览已失效");
    fireEvent.click(screen.getByRole("button", { name: "重新生成数据预览" }));

    await waitFor(() => {
      expect(mutationRequests(fetchSpy, "/preview")).toHaveLength(1);
    });
    const previewCall = mutationRequests(fetchSpy, "/preview")[0];
    expect(JSON.parse(String(previewCall?.[1]?.body))).toEqual({
      rebuild: true,
    });
    expect(mutationRequests(fetchSpy, "/confirm")).toHaveLength(0);
  });

  it("retries a failed Job through the generic retry endpoint", async () => {
    const fetchSpy = installBulkApi({
      getJob: () => makeJob({ status: "failed" }),
      mutation: (path) =>
        path.endsWith("/import-jobs/job-1/retry")
          ? success(dispatch("confirm_queued"), 202)
          : undefined,
    });

    renderBulk();
    fireEvent.click(await screen.findByRole("button", { name: /重\s*试/ }));

    await waitFor(() => {
      expect(mutationRequests(fetchSpy, "/retry")).toHaveLength(1);
    });
    expect(mutationRequests(fetchSpy, "/preview")).toHaveLength(0);
    expect(mutationRequests(fetchSpy, "/confirm")).toHaveLength(0);
  });

  it.each([
    ["confirm_queued", "正在准备导入"],
    ["importing", "正在导入"],
  ] satisfies [ImportJobStatus, string][])(
    "shows only the real %s state without fabricated progress",
    async (status, label) => {
      const fetchSpy = installBulkApi({
        getJob: () =>
          makeJob({
            status,
            confirmed_revision: 4,
            confirmed_at: "2026-08-13T10:00:00Z",
          }),
      });

      renderBulk();
      expect(await screen.findByText(label)).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "确认导入" }),
      ).not.toBeInTheDocument();
      expect(mutationRequests(fetchSpy, "/confirm")).toHaveLength(0);
      expect(document.body.textContent).not.toMatch(/\b\d+%\b/);
      expect(document.body.textContent).not.toMatch(/\d+\s*\/\s*\d+/);
      expect(document.body.textContent).not.toContain("已写入");
      expect(document.body.textContent).not.toContain("部分成功");
    },
  );

  it("renders the exact completed result separately from the Preview summary", async () => {
    const result = {
      import_job_id: "job-1",
      preview_revision: 4,
      created_rows: 1_994,
      updated_rows: 1,
      no_change_rows: 7,
      skipped_rows: 2,
      error_rows: 3,
      manual_review_rows: 4,
    };
    installBulkApi({
      getJob: () =>
        makeJob({
          status: "completed",
          result,
          confirmed_revision: 4,
          confirmed_at: "2026-08-13T10:00:00Z",
          completed_at: "2026-08-13T10:00:02Z",
        }),
    });

    renderBulk();
    await screen.findByText("✓ 导入完成");
    const resultSection = document.querySelector(
      ".bulk-preview-completed-result",
    );
    expect(resultSection).not.toBeNull();
    const actual = within(resultSection as HTMLElement);
    for (const [label, value] of [
      ["新增", "1,994"],
      ["更新", "1"],
      ["无需变更", "7"],
      ["跳过", "2"],
      ["需人工处理", "4"],
      ["错误", "3"],
    ] as const) {
      expect(actual.getByText(label)).toBeInTheDocument();
      expect(actual.getByText(value)).toBeInTheDocument();
    }
    expect(screen.getByText("完成时间：2026-08-13 18:00")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看达人库" })).toHaveAttribute(
      "href",
      "/influencers",
    );
    expect(actual.queryByText("20")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("成功率");
    expect(document.body.textContent).not.toContain("预计耗时");
  });
});
