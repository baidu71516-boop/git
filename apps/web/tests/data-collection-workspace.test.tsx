import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DataCollectionWorkspace } from "@/features/imports/data-collection-workspace";
import type {
  CollectionJobPublic,
  ImportJobFilePublic,
  ImportJobPublic,
} from "@/features/imports/types";

const navigation = vi.hoisted(() => ({
  pathname: "/",
  search: "",
  replaceCalls: [] as string[],
  pushCalls: [] as string[],
  listeners: new Set<() => void>(),
}));

vi.mock("next/navigation", async () => {
  const React = await import("react");

  function updateLocation(href: string, calls: string[]) {
    const next = new URL(href, "http://localhost");
    navigation.pathname = next.pathname;
    navigation.search = next.search.slice(1);
    calls.push(`${next.pathname}${next.search}`);
    navigation.listeners.forEach((listener) => listener());
  }

  return {
    usePathname: () => navigation.pathname,
    useRouter: () => ({
      replace: (href: string) => updateLocation(href, navigation.replaceCalls),
      push: (href: string) => updateLocation(href, navigation.pushCalls),
    }),
    useSearchParams: () => {
      const search = React.useSyncExternalStore(
        (listener) => {
          navigation.listeners.add(listener);
          return () => navigation.listeners.delete(listener);
        },
        () => navigation.search,
        () => navigation.search,
      );
      return React.useMemo(() => new URLSearchParams(search), [search]);
    },
  };
});

vi.mock("@/components/import-workspace", () => ({
  ImportWorkspace: () => {
    const [draft, setDraft] = useState("");
    return (
      <label>
        Legacy 草稿
        <input
          aria-label="Legacy 草稿"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      </label>
    );
  },
}));

const collection: CollectionJobPublic = {
  id: "collection-1",
  name: "真实采集任务",
  industry: "美妆",
  subdirection: null,
  purpose: "商务开发",
  target_action: "邮件触达",
  follower_min: null,
  follower_max: null,
  target_count: 50,
  department_id: "department-1",
  owner_operator_id: "operator-1",
  source_type: "manual_huitun_export",
  status: "active",
  notes: null,
  screening_rules: {
    schema_version: 1,
    platforms: ["xiaohongshu"],
    source_tags_exact_any: [],
  },
  screening_rules_revision: 1,
  created_at: "2026-08-10T00:00:00Z",
  updated_at: "2026-08-10T00:00:00Z",
};

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
    status: "draft",
    detected_fields: null,
    field_mapping: null,
    mapping_hash: null,
    preview_revision: 0,
    preview_summary: null,
    result: null,
    total_rows: 0,
    valid_rows: 0,
    warning_rows: 0,
    error_rows: 0,
    created_rows: 0,
    updated_rows: 0,
    no_change_rows: 0,
    skipped_rows: 0,
    manual_review_rows: 0,
    confirmed_revision: null,
    confirmed_at: null,
    completed_at: null,
    error_code: null,
    error_message: null,
    created_at: "2026-08-10T00:00:00Z",
    updated_at: "2026-08-10T00:00:00Z",
    ...overrides,
  };
}

function makeFile(
  overrides: Partial<ImportJobFilePublic> = {},
): ImportJobFilePublic {
  return {
    id: "file-1",
    import_job_id: "job-1",
    stored_file_id: "stored-1",
    position: 1,
    original_filename: "ready.csv",
    declared_mime: "text/csv",
    status: "ready",
    source_acquired_at: "2026-08-10T02:00:00Z",
    source_acquired_at_origin: "user_confirmed",
    source_acquired_at_confirmation_required: false,
    detected_type: "csv",
    detected_mime: "text/csv",
    file_size: 128,
    sha256: "a".repeat(64),
    detected_fields: ["达人名称"],
    field_mapping: { 达人名称: "nickname" },
    mapping_hash: "b".repeat(64),
    raw_rows: 1,
    warning_rows: 0,
    error_rows: 0,
    error_code: null,
    error_message: null,
    parse_task_id: null,
    parse_attempts: 1,
    parse_started_at: "2026-08-10T00:00:00Z",
    parse_completed_at: "2026-08-10T00:00:01Z",
    excluded_at: null,
    created_at: "2026-08-10T00:00:00Z",
    updated_at: "2026-08-10T00:00:01Z",
    ...overrides,
  };
}

function envelope(data: unknown, status = 200): Response {
  return new Response(
    JSON.stringify({
      success: status < 400,
      data: status < 400 ? data : null,
      error:
        status < 400
          ? null
          : {
              code: "IMPORT_JOB_NOT_FOUND",
              message: "not found",
              details: null,
            },
      request_id: "request-1",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function apiError(status: number, code: string): Response {
  return new Response(
    JSON.stringify({
      success: false,
      data: null,
      error: { code, message: code, details: null },
      request_id: "request-1",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function renderWorkspace(role: "operator" | "viewer" = "operator") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <DataCollectionWorkspace role={role} />
    </QueryClientProvider>,
  );
  return { ...result, queryClient };
}

function apiRouter(job: ImportJobPublic, files: ImportJobFilePublic[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith(`/import-jobs/${job.id}`)) return envelope(job);
    if (url.endsWith(`/import-jobs/${job.id}/files`)) return envelope(files);
    if (url.endsWith(`/collection-jobs/${collection.id}`)) {
      return envelope(collection);
    }
    throw new Error(`Unexpected request: ${url}`);
  });
}

function countRequests(fetchSpy: ReturnType<typeof apiRouter>, suffix: string) {
  return fetchSpy.mock.calls.filter(([input]) => String(input).endsWith(suffix))
    .length;
}

beforeEach(() => {
  navigation.pathname = "/";
  navigation.search = "";
  navigation.replaceCalls.length = 0;
  navigation.pushCalls.length = 0;
});

afterEach(() => {
  cleanup();
  navigation.listeners.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("DataCollectionWorkspace", () => {
  it("defaults to Legacy, syncs Tabs to the URL, and preserves both mounted panes without storage or a Job list", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new Error("No request expected"));
    const getItemSpy = vi.spyOn(Storage.prototype, "getItem");
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");

    renderWorkspace();

    expect(screen.getByRole("tab", { name: "单文件导入" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    fireEvent.change(screen.getByLabelText("Legacy 草稿"), {
      target: { value: "保留这段输入" },
    });

    fireEvent.click(screen.getByRole("tab", { name: "批量文件处理" }));
    expect(await screen.findByText("还没有批量采集任务")).toBeInTheDocument();
    expect(navigation.replaceCalls.at(-1)).toBe("/?workspace=bulk");

    fireEvent.click(screen.getByRole("tab", { name: "单文件导入" }));
    expect(screen.getByLabelText("Legacy 草稿")).toHaveValue("保留这段输入");
    fireEvent.click(screen.getByRole("tab", { name: "批量文件处理" }));
    expect(screen.getByText("还没有批量采集任务")).toBeInTheDocument();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(getItemSpy).not.toHaveBeenCalled();
    expect(setItemSpy).not.toHaveBeenCalled();
  });

  it("writes the real Bulk Job ID to the URL only after both creation steps succeed", async () => {
    navigation.search = "workspace=bulk";
    const createdJob = makeJob({ id: "job-created" });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/collection-jobs") && init?.method === "POST") {
          return envelope(collection, 201);
        }
        if (url.endsWith("/import-jobs/bulk") && init?.method === "POST") {
          return envelope(createdJob, 201);
        }
        if (url.endsWith("/import-jobs/job-created")) {
          return envelope(createdJob);
        }
        if (url.endsWith("/import-jobs/job-created/files")) {
          return envelope([]);
        }
        if (url.endsWith(`/collection-jobs/${collection.id}`)) {
          return envelope(collection);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "新建采集任务" }));
    fireEvent.change(screen.getByLabelText("任务名称"), {
      target: { value: collection.name },
    });
    fireEvent.change(screen.getByLabelText("行业"), {
      target: { value: collection.industry },
    });
    fireEvent.change(screen.getByLabelText("采集目的"), {
      target: { value: collection.purpose },
    });
    fireEvent.change(screen.getByLabelText("目标动作"), {
      target: { value: collection.target_action },
    });
    const submit = screen.getByRole("button", {
      name: "创建并开始批量处理",
    });
    const form = submit.closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.filter(
          ([input, init]) =>
            String(input).endsWith("/collection-jobs") &&
            init?.method === "POST",
        ),
      ).toHaveLength(1),
    );
    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.filter(
          ([input, init]) =>
            String(input).endsWith("/import-jobs/bulk") &&
            init?.method === "POST",
        ),
      ).toHaveLength(1),
    );
    await waitFor(() =>
      expect(navigation.pushCalls).toContain(
        "/?workspace=bulk&bulk_job_id=job-created",
      ),
    );
    expect(
      fetchSpy.mock.calls.filter(
        ([input, init]) =>
          String(input).endsWith("/collection-jobs") && init?.method === "POST",
      ),
    ).toHaveLength(1);
    expect(
      fetchSpy.mock.calls.filter(
        ([input, init]) =>
          String(input).endsWith("/import-jobs/bulk") &&
          init?.method === "POST",
      ),
    ).toHaveLength(1);
  });

  it("carries Queue context in the URL and binds it exactly once when creating the Bulk Job", async () => {
    navigation.search = "workspace=bulk&refresh_queue_id=queue-1";
    const createdJob = makeJob({
      id: "job-refresh",
      refresh_queue_id: "queue-1",
    });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/refresh-queues/queue-1") && !init?.method) {
          return envelope({
            queue: {
              id: "queue-1",
              department_id: "department-1",
              created_by_operator_id: "operator-1",
              status: "exported",
              as_of: "2026-08-14T00:00:00Z",
              requested_limit: 10,
              today_total_limit: 10,
              refresh_limit: 10,
              policy_version: 1,
              criteria_snapshot: {},
              created_at: "2026-08-14T00:00:00Z",
              updated_at: "2026-08-14T00:00:00Z",
              exported_at: "2026-08-14T01:00:00Z",
              completed_at: null,
              cancelled_at: null,
            },
            summary: {
              requested: 10,
              selected: 10,
              unique_influencers: 10,
              freshness_breakdown: { stale: 10 },
              priority_breakdown: { "3": 10 },
              status_breakdown: { pending: 10 },
            },
          });
        }
        if (url.endsWith("/collection-jobs") && init?.method === "POST") {
          return envelope(collection, 201);
        }
        if (url.endsWith("/import-jobs/bulk") && init?.method === "POST") {
          return envelope(createdJob, 201);
        }
        if (url.endsWith("/import-jobs/job-refresh")) {
          return envelope(createdJob);
        }
        if (url.endsWith("/import-jobs/job-refresh/files")) return envelope([]);
        if (url.endsWith(`/collection-jobs/${collection.id}`)) {
          return envelope(collection);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderWorkspace();
    expect(await screen.findByText("更新名单回流")).toBeInTheDocument();
    expect(
      await screen.findByText("当前批次将用于处理已关联的数据更新名单。"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "退出回流模式" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新建采集任务" }));
    fireEvent.change(screen.getByLabelText("任务名称"), {
      target: { value: collection.name },
    });
    fireEvent.change(screen.getByLabelText("行业"), {
      target: { value: collection.industry },
    });
    fireEvent.change(screen.getByLabelText("采集目的"), {
      target: { value: collection.purpose },
    });
    fireEvent.change(screen.getByLabelText("目标动作"), {
      target: { value: collection.target_action },
    });
    fireEvent.submit(
      screen
        .getByRole("button", { name: "创建并开始批量处理" })
        .closest("form") as HTMLFormElement,
    );

    await waitFor(() => {
      const createCall = fetchSpy.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/import-jobs/bulk") &&
          init?.method === "POST",
      );
      expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
        collection_job_id: "collection-1",
        refresh_queue_id: "queue-1",
      });
    });
    await waitFor(() =>
      expect(navigation.pushCalls).toContain(
        "/?workspace=bulk&refresh_queue_id=queue-1&bulk_job_id=job-refresh",
      ),
    );
    expect(
      screen.queryByRole("button", { name: "退出回流模式" }),
    ).not.toBeInTheDocument();
  });

  it("allows exiting Queue return mode before a Bulk Job exists", async () => {
    navigation.search = "workspace=bulk&refresh_queue_id=queue-1";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      envelope({
        queue: { id: "queue-1", status: "exported" },
        summary: { status_breakdown: { pending: 1 } },
      }),
    );

    renderWorkspace();
    fireEvent.click(
      await screen.findByRole("button", { name: "退出回流模式" }),
    );

    await waitFor(() => expect(navigation.search).toBe("workspace=bulk"));
  });

  it("recovers only the URL Job, retains its ID across Tab changes, and never calls a list endpoint", async () => {
    navigation.search = "workspace=bulk&bulk_job_id=job-1";
    const fetchSpy = apiRouter(makeJob(), [makeFile()]);
    const getItemSpy = vi.spyOn(Storage.prototype, "getItem");
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");

    renderWorkspace();

    expect(await screen.findByText("真实采集任务")).toBeInTheDocument();
    expect(screen.getByText("ready.csv")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "单文件导入" }));
    await waitFor(() => expect(navigation.search).toBe("bulk_job_id=job-1"));
    fireEvent.click(screen.getByRole("tab", { name: "批量文件处理" }));
    await waitFor(() =>
      expect(navigation.search).toBe("bulk_job_id=job-1&workspace=bulk"),
    );

    const requestPaths = fetchSpy.mock.calls.map(([input]) => String(input));
    expect(requestPaths).toContain("/api/v1/import-jobs/job-1");
    expect(requestPaths).toContain("/api/v1/import-jobs/job-1/files");
    expect(requestPaths).toContain("/api/v1/collection-jobs/collection-1");
    expect(requestPaths).not.toContain("/api/v1/import-jobs");
    expect(requestPaths.every((path) => !path.includes("localStorage"))).toBe(
      true,
    );
    expect(getItemSpy).not.toHaveBeenCalled();
    expect(setItemSpy).not.toHaveBeenCalled();
  });

  it("lets a Viewer inspect all real screening fields without exposing a save action", async () => {
    navigation.search = "workspace=bulk&bulk_job_id=job-1";
    const fetchSpy = apiRouter(makeJob(), [makeFile()]);

    renderWorkspace("viewer");
    fireEvent.click(
      await screen.findByRole("button", { name: "查看筛选规则" }),
    );

    const dialog = await screen.findByRole("dialog", {
      name: "查看筛选规则",
    });
    expect(screen.getByText("筛选规则摘要")).toBeInTheDocument();
    expect(screen.getByText(/平台：小红书/)).toBeInTheDocument();
    expect(screen.getByText(/来源标签：未设置/)).toBeInTheDocument();
    expect(screen.getByText(/粉丝范围：不限/)).toBeInTheDocument();
    expect(screen.getByText(/规则版本：1/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("小红书")).toBeChecked();
    expect(within(dialog).getByLabelText("来源标签")).toBeDisabled();
    expect(
      within(dialog).queryByRole("button", { name: "保存筛选规则" }),
    ).not.toBeInTheDocument();
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
      false,
    );
  });

  it("saves the five real rule fields, refetches both jobs, and reuses Backend stale UI without automation", async () => {
    navigation.search = "workspace=bulk&bulk_job_id=job-1";
    let currentJob = makeJob({
      status: "preview_ready",
      preview_revision: 1,
    });
    let currentCollection = { ...collection };
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/import-jobs/job-1") && !init?.method) {
          return envelope(currentJob);
        }
        if (url.endsWith("/import-jobs/job-1/files") && !init?.method) {
          return envelope([makeFile()]);
        }
        if (url.endsWith("/collection-jobs/collection-1") && !init?.method) {
          return envelope(currentCollection);
        }
        if (
          url.endsWith("/collection-jobs/collection-1/screening-rules") &&
          init?.method === "PUT"
        ) {
          currentCollection = {
            ...currentCollection,
            follower_min: 10_000,
            follower_max: null,
            screening_rules: {
              schema_version: 1,
              platforms: ["xiaohongshu"],
              source_tags_exact_any: ["美妆", "护肤"],
            },
            screening_rules_revision: 2,
          };
          currentJob = { ...currentJob, status: "preview_stale" };
          return envelope(currentCollection);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderWorkspace();
    fireEvent.click(
      await screen.findByRole("button", { name: "编辑筛选规则" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "编辑筛选规则",
    });
    expect(
      within(dialog).getByText("保存后，当前数据预览将失效，需要重新生成。"),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("来源标签"), {
      target: { value: "美妆\n护肤" },
    });
    fireEvent.change(within(dialog).getByPlaceholderText("最低粉丝（不限）"), {
      target: { value: "10000" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "保存筛选规则" }),
    );

    await waitFor(() => {
      const put = fetchSpy.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith(
            "/collection-jobs/collection-1/screening-rules",
          ) && init?.method === "PUT",
      );
      expect(JSON.parse(String(put?.[1]?.body))).toEqual({
        screening_rules: {
          schema_version: 1,
          platforms: ["xiaohongshu"],
          source_tags_exact_any: ["美妆", "护肤"],
        },
        follower_min: 10_000,
        follower_max: null,
        expected_revision: 1,
      });
    });
    expect(await screen.findByText("筛选规则已保存。")).toBeInTheDocument();
    expect(await screen.findByText("数据预览需要重新生成")).toBeInTheDocument();

    const requestPaths = fetchSpy.mock.calls.map(([input]) => String(input));
    expect(
      requestPaths.filter((path) => path.endsWith("/import-jobs/job-1")).length,
    ).toBeGreaterThan(1);
    expect(
      requestPaths.filter((path) =>
        path.endsWith("/collection-jobs/collection-1"),
      ).length,
    ).toBeGreaterThan(1);
    expect(requestPaths.some((path) => path.endsWith("/preview"))).toBe(false);
    expect(requestPaths.some((path) => path.endsWith("/confirm"))).toBe(false);
  });

  it("shows a revision conflict and reloads latest rules without retrying or overwriting", async () => {
    navigation.search = "workspace=bulk&bulk_job_id=job-1";
    let latest = false;
    let collectionReads = 0;
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/import-jobs/job-1")) return envelope(makeJob());
        if (url.endsWith("/import-jobs/job-1/files")) {
          return envelope([makeFile()]);
        }
        if (url.endsWith("/collection-jobs/collection-1") && !init?.method) {
          collectionReads += 1;
          return envelope(
            latest
              ? {
                  ...collection,
                  screening_rules: {
                    ...collection.screening_rules,
                    source_tags_exact_any: ["护肤"],
                  },
                  screening_rules_revision: 3,
                }
              : collection,
          );
        }
        if (
          url.endsWith("/collection-jobs/collection-1/screening-rules") &&
          init?.method === "PUT"
        ) {
          latest = true;
          return apiError(409, "SCREENING_RULES_REVISION_CONFLICT");
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderWorkspace();
    fireEvent.click(
      await screen.findByRole("button", { name: "编辑筛选规则" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存筛选规则" }));

    expect(
      await screen.findByText(
        "筛选规则已被其他人更新，请重新加载最新规则后再继续编辑。",
      ),
    ).toBeInTheDocument();
    expect(
      fetchSpy.mock.calls.filter(([, init]) => init?.method === "PUT"),
    ).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "重新加载最新规则" }));
    await waitFor(() => expect(collectionReads).toBeGreaterThan(1));
    await waitFor(() =>
      expect(screen.getByLabelText("来源标签")).toHaveValue("护肤"),
    );
    expect(screen.getByDisplayValue("3")).toBeDisabled();
    expect(
      fetchSpy.mock.calls.filter(([, init]) => init?.method === "PUT"),
    ).toHaveLength(1);
  }, 20_000);

  it("does not poll an idle Bulk pane after it is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    navigation.search = "workspace=bulk&bulk_job_id=job-1";
    const fetchSpy = apiRouter(makeJob({ status: "draft" }), [makeFile()]);

    renderWorkspace();
    await waitFor(() =>
      expect(screen.getByText("真实采集任务")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("tab", { name: "单文件导入" }));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "单文件导入" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    const jobReads = countRequests(fetchSpy, "/import-jobs/job-1");
    const fileReads = countRequests(fetchSpy, "/import-jobs/job-1/files");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });

    expect(countRequests(fetchSpy, "/import-jobs/job-1")).toBe(jobReads);
    expect(countRequests(fetchSpy, "/import-jobs/job-1/files")).toBe(fileReads);
  });

  it("keeps polling only the genuine active Job while the Bulk pane is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    navigation.search = "workspace=bulk&bulk_job_id=job-1";
    const fetchSpy = apiRouter(makeJob({ status: "previewing" }), [makeFile()]);

    renderWorkspace();
    await waitFor(() =>
      expect(screen.getByText("正在生成数据预览")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("tab", { name: "单文件导入" }));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "单文件导入" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    const jobReads = countRequests(fetchSpy, "/import-jobs/job-1");
    const fileReads = countRequests(fetchSpy, "/import-jobs/job-1/files");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_300);
    });
    await waitFor(() =>
      expect(countRequests(fetchSpy, "/import-jobs/job-1")).toBeGreaterThan(
        jobReads,
      ),
    );
    expect(countRequests(fetchSpy, "/import-jobs/job-1/files")).toBe(fileReads);
  });

  it("keeps polling only genuine parsing files while a draft Bulk pane is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    navigation.search = "workspace=bulk&bulk_job_id=job-1";
    const fetchSpy = apiRouter(makeJob({ status: "draft" }), [
      makeFile({ status: "parsing" }),
    ]);

    renderWorkspace();
    await waitFor(() => expect(screen.getByText("处理中")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "单文件导入" }));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "单文件导入" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    const jobReads = countRequests(fetchSpy, "/import-jobs/job-1");
    const fileReads = countRequests(fetchSpy, "/import-jobs/job-1/files");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_300);
    });
    await waitFor(() =>
      expect(
        countRequests(fetchSpy, "/import-jobs/job-1/files"),
      ).toBeGreaterThan(fileReads),
    );
    expect(countRequests(fetchSpy, "/import-jobs/job-1")).toBe(jobReads);
  });
});
