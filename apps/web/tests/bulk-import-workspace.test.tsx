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
import type { Key, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BulkImportWorkspaceView } from "@/features/imports/bulk-import-workspace-view";
import { BulkImportWorkspace } from "@/features/imports/bulk-import-workspace";
import { AMBIGUOUS_BULK_CREATE_MESSAGE } from "@/features/imports/formatters";
import type {
  CollectionJobPublic,
  ImportDispatchResult,
  ImportJobFilePublic,
  ImportJobFileStatus,
  ImportJobPublic,
  ImportJobStatus,
} from "@/features/imports/types";

type TestTableColumn = {
  key?: Key;
  title?: ReactNode;
  dataIndex?: string | string[];
  render?: (value: unknown, record: unknown, index: number) => ReactNode;
};

function tableValue(record: unknown, dataIndex: TestTableColumn["dataIndex"]) {
  const keys = Array.isArray(dataIndex)
    ? dataIndex
    : typeof dataIndex === "string"
      ? [dataIndex]
      : [];
  return keys.reduce<unknown>(
    (value, key) =>
      value && typeof value === "object"
        ? (value as Record<string, unknown>)[key]
        : undefined,
    record,
  );
}

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    Table: ({
      className,
      columns = [],
      dataSource = [],
      rowKey,
      onRow,
    }: {
      className?: string;
      columns?: TestTableColumn[];
      dataSource?: unknown[];
      rowKey?: string | ((record: unknown) => Key);
      onRow?: (record: unknown, index: number) => { className?: string };
    }) => (
      <table className={className}>
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th key={column.key ?? index}>{column.title}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {dataSource.map((record, rowIndex) => {
            const row = onRow?.(record, rowIndex);
            const key =
              typeof rowKey === "function"
                ? rowKey(record)
                : typeof rowKey === "string" &&
                    record &&
                    typeof record === "object"
                  ? (record as Record<string, Key>)[rowKey]
                  : rowIndex;
            return (
              <tr className={row?.className} key={key}>
                {columns.map((column, columnIndex) => {
                  const value = tableValue(record, column.dataIndex);
                  return (
                    <td key={column.key ?? columnIndex}>
                      {column.render?.(value, record, rowIndex) ??
                        (value as ReactNode)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    ),
  };
});

const queryClients: QueryClient[] = [];
const nativeMessageChannel = globalThis.MessageChannel;
const messageChannels = new Set<MessageChannel>();
const bulkFileTableMode = vi.hoisted(() => ({ compact: false }));
const routerPush = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

vi.mock(
  "@/features/imports/components/bulk-file-table",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("@/features/imports/components/bulk-file-table")
      >();
    return {
      ...actual,
      BulkFileTable: (props: {
        files: Array<{
          id: string;
          original_filename: string;
          status: string;
          source_acquired_at_confirmation_required: boolean;
        }>;
        readOnly: boolean;
        frozen: boolean;
        busyFileId: string | null;
        onEditAcquisitionTime: (file: never) => void;
        onEditMapping: (file: never) => void;
        onRetry: (file: never) => void;
        onExclude: (file: never) => void;
      }) =>
        bulkFileTableMode.compact ? (
          <table className="bulk-file-table">
            <tbody>
              {props.files.map((file) => {
                const disabled =
                  props.readOnly || props.frozen || props.busyFileId !== null;
                return (
                  <tr key={file.id}>
                    <td>
                      {file.original_filename}
                      {file.source_acquired_at_confirmation_required ? (
                        <span>待确认</span>
                      ) : null}
                    </td>
                    <td>
                      {file.status === "mapping_required" ? (
                        <button
                          disabled={disabled}
                          onClick={() => props.onEditMapping(file as never)}
                          type="button"
                        >
                          处理字段映射
                        </button>
                      ) : null}
                      {file.status === "failed" ? (
                        <button
                          disabled={disabled}
                          onClick={() => props.onRetry(file as never)}
                          type="button"
                        >
                          重试
                        </button>
                      ) : null}
                      {["ready", "mapping_required", "failed"].includes(
                        file.status,
                      ) ? (
                        <button
                          disabled={disabled}
                          onClick={() =>
                            props.onEditAcquisitionTime(file as never)
                          }
                          type="button"
                        >
                          修改时间
                        </button>
                      ) : null}
                      {[
                        "uploaded",
                        "ready",
                        "mapping_required",
                        "failed",
                      ].includes(file.status) ? (
                        <button
                          disabled={disabled}
                          onClick={() => props.onExclude(file as never)}
                          type="button"
                        >
                          排除
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          actual.BulkFileTable(props as never)
        ),
    };
  },
);

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
    source_acquired_at: "2026-08-10T10:00:00+08:00",
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
    parse_started_at: "2026-08-10T02:00:00Z",
    parse_completed_at: "2026-08-10T02:00:01Z",
    excluded_at: null,
    created_at: "2026-08-10T02:00:00Z",
    updated_at: "2026-08-10T02:00:01Z",
    ...overrides,
  };
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

function failure(code: string, status: number, message = "technical detail") {
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

function uploadResult(file: ImportJobFilePublic) {
  return { file, idempotent: false };
}

function dispatch(
  status: ImportJobStatus = "previewing",
): ImportDispatchResult {
  return {
    import_job_id: "job-1",
    status,
    preview_revision: status === "preview_ready" ? 1 : 0,
    task_id: "task-1",
    idempotent: false,
  };
}

function renderWithClient(node: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      // Hooks must override this: mutation requests in UI-4B1 never auto-retry.
      mutations: { retry: 3, retryDelay: 0, gcTime: Infinity },
    },
  });
  queryClients.push(queryClient);
  const result = render(
    <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>,
  );
  return { ...result, queryClient };
}

function renderBulk({
  jobId = null,
  role = "operator",
  onSelectJob = vi.fn(),
  onClearJob = vi.fn(),
}: {
  jobId?: string | null;
  role?: "super_admin" | "manager" | "operator" | "viewer";
  onSelectJob?: (jobId: string) => void;
  onClearJob?: () => void;
} = {}) {
  return {
    ...renderWithClient(
      <BulkImportWorkspace
        role={role}
        jobId={jobId}
        onSelectJob={onSelectJob}
        onClearJob={onClearJob}
      />,
    ),
    onSelectJob,
    onClearJob,
  };
}

function recoveredRouter(
  job: ImportJobPublic,
  files: ImportJobFilePublic[],
  mutation?: (
    url: string,
    init: RequestInit | undefined,
  ) => Response | Promise<Response> | undefined,
) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      const mutationResponse = mutation?.(url, init);
      if (mutationResponse) return mutationResponse;
      if (url.endsWith(`/import-jobs/${job.id}`)) return success(job);
      if (url.endsWith(`/import-jobs/${job.id}/files`)) return success(files);
      if (url.endsWith(`/collection-jobs/${job.collection_job_id}`)) {
        return success(collection);
      }
      throw new Error(`Unexpected request: ${url}`);
    });
}

function fillCollectionForm() {
  fireEvent.change(screen.getByLabelText("任务名称"), {
    target: { value: "真实采集任务" },
  });
  fireEvent.change(screen.getByLabelText("行业"), {
    target: { value: "美妆" },
  });
  fireEvent.change(screen.getByLabelText("采集目的"), {
    target: { value: "商务开发" },
  });
  fireEvent.change(screen.getByLabelText("目标动作"), {
    target: { value: "邮件触达" },
  });
}

function requestCount(
  fetchSpy: ReturnType<typeof recoveredRouter>,
  suffix: string,
  method?: string,
) {
  return fetchSpy.mock.calls.filter(
    ([input, init]) =>
      String(input).endsWith(suffix) &&
      (method === undefined || (init?.method ?? "GET") === method),
  ).length;
}

function rowFor(filename: string): HTMLElement {
  const row = screen.getByText(filename).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

async function bulkFileTable(container: HTMLElement) {
  await waitFor(() =>
    expect(container.querySelector(".bulk-file-table")).not.toBeNull(),
  );
  const table = container.querySelector<HTMLElement>(".bulk-file-table");
  if (!table) throw new Error("Bulk file table was not rendered");
  return table;
}

function fileRow(table: HTMLElement, filename: string) {
  const row = within(table).getByText(filename).closest("tr");
  if (!row) throw new Error(`Bulk file row was not rendered: ${filename}`);
  return row as HTMLElement;
}

async function bulkMenuAction(fileId: string, name: string) {
  return await waitFor(() => {
    const action = Array.from(
      document.querySelectorAll<HTMLButtonElement>(
        `button[data-file-id="${fileId}"]`,
      ),
    ).find((button) => button.textContent?.trim() === name);
    if (!action) throw new Error(`Bulk menu action was not rendered: ${name}`);
    return action;
  });
}

async function exclusionConfirm() {
  return await waitFor(() => {
    const popover = Array.from(
      document.querySelectorAll<HTMLElement>(".ant-popover"),
    ).find((candidate) =>
      candidate.textContent?.includes("确认排除这个文件？"),
    );
    if (!popover)
      throw new Error("Bulk exclusion confirmation was not rendered");
    return popover;
  });
}

async function mappingOption(title: string) {
  return await waitFor(() => {
    const option = document.querySelector<HTMLElement>(
      `.ant-select-item-option[title="${title}"]`,
    );
    if (!option) throw new Error(`Mapping option was not rendered: ${title}`);
    return option;
  });
}

function viewProps(
  job: ImportJobPublic,
  files: ImportJobFilePublic[],
  overrides: Partial<React.ComponentProps<typeof BulkImportWorkspaceView>> = {},
) {
  return {
    job,
    collection,
    files,
    readOnly: false,
    uploadItems: [],
    busyFileId: null,
    previewBusy: false,
    error: null,
    notice: null,
    onNewCollection: vi.fn(),
    onSelectFiles: vi.fn(),
    onRetryUpload: vi.fn(),
    onEditAcquisitionTime: vi.fn(),
    onEditMapping: vi.fn(),
    onRetryFile: vi.fn(),
    onExcludeFile: vi.fn(),
    onRequestPreview: vi.fn(),
    onRebuildPreview: vi.fn(),
    onRetryJob: vi.fn(),
    ...overrides,
  } satisfies React.ComponentProps<typeof BulkImportWorkspaceView>;
}

beforeEach(() => {
  bulkFileTableMode.compact = false;
  document.cookie = "outreach_csrf=test-csrf; path=/";
  globalThis.MessageChannel = class extends nativeMessageChannel {
    constructor() {
      super();
      messageChannels.add(this);
    }
  };
});

afterEach(async () => {
  const clients = queryClients.splice(0);

  try {
    cleanup();
    await act(async () => {
      await Promise.resolve();
    });
    await Promise.all(clients.map((client) => client.cancelQueries()));
    for (const client of clients) {
      expect(client.isFetching()).toBe(0);
      expect(client.isMutating()).toBe(0);
    }
  } finally {
    for (const client of clients) client.clear();
    for (const channel of messageChannels) {
      channel.port1.close();
      channel.port2.close();
    }
    messageChannels.clear();
    bulkFileTableMode.compact = false;
    globalThis.MessageChannel = nativeMessageChannel;
    vi.useRealTimers();
    vi.restoreAllMocks();
    document.cookie = "outreach_csrf=; Max-Age=0; path=/";
  }
});

describe("BulkImportWorkspace creation hardening", () => {
  it("shows the frozen safe copy for an ambiguous step-two result and never replays either POST", async () => {
    let bulkCreates = 0;
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/collection-jobs") && init?.method === "POST") {
          return success(collection, 201);
        }
        if (url.endsWith("/import-jobs/bulk") && init?.method === "POST") {
          bulkCreates += 1;
          return failure("INTERNAL_ERROR", 503, "broker stack trace");
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderBulk();
    fireEvent.click(screen.getByRole("button", { name: "新建采集任务" }));
    fillCollectionForm();
    fireEvent.click(screen.getByRole("button", { name: "创建并开始批量处理" }));

    expect(
      await screen.findByText(AMBIGUOUS_BULK_CREATE_MESSAGE),
    ).toBeInTheDocument();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(requestCount(fetchSpy, "/collection-jobs", "POST")).toBe(1);
    expect(requestCount(fetchSpy, "/import-jobs/bulk", "POST")).toBe(1);
    expect(bulkCreates).toBe(1);
    expect(
      screen.queryByRole("button", { name: "继续创建批量任务" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("broker stack trace")).not.toBeInTheDocument();
  });

  it("retries only deterministic step two with the retained Collection and selects the created Job", async () => {
    let bulkCreates = 0;
    const createdJob = makeJob({ id: "job-created" });
    const onSelectJob = vi.fn();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/collection-jobs") && init?.method === "POST") {
          return success(collection, 201);
        }
        if (url.endsWith("/import-jobs/bulk") && init?.method === "POST") {
          bulkCreates += 1;
          return bulkCreates === 1
            ? failure("INVALID_STATE_TRANSITION", 409)
            : success(createdJob, 201);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderBulk({ onSelectJob });
    fireEvent.click(screen.getByRole("button", { name: "新建采集任务" }));
    fillCollectionForm();
    fireEvent.click(screen.getByRole("button", { name: "创建并开始批量处理" }));

    const continueButton = await screen.findByRole("button", {
      name: "继续创建批量任务",
    });
    expect(screen.getByText(/后续操作只会继续创建/)).toBeInTheDocument();
    fireEvent.click(continueButton);

    await waitFor(() =>
      expect(onSelectJob).toHaveBeenCalledWith("job-created"),
    );
    expect(requestCount(fetchSpy, "/collection-jobs", "POST")).toBe(1);
    expect(requestCount(fetchSpy, "/import-jobs/bulk", "POST")).toBe(2);
    const secondBulkBody = fetchSpy.mock.calls.filter(([input]) =>
      String(input).endsWith("/import-jobs/bulk"),
    )[1]?.[1]?.body;
    expect(JSON.parse(String(secondBulkBody))).toEqual({
      collection_job_id: collection.id,
    });
  });
});

describe("BulkImportWorkspace URL recovery", () => {
  it.each([
    [
      "forbidden-job",
      403,
      "PERMISSION_DENIED",
      "当前账号没有执行此操作的权限。",
    ],
    [
      "missing-job",
      404,
      "IMPORT_JOB_NOT_FOUND",
      "批量文件处理任务不存在或当前账号无法访问。",
    ],
    ["not-a-uuid", 422, "VALIDATION_ERROR", "提交内容有误，请检查后重试。"],
  ] as const)(
    "renders a recoverable formal error for %s without guessing a Job list",
    async (jobId, status, code, safeCopy) => {
      const onClearJob = vi.fn();
      const fetchSpy = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(failure(code, status, "raw technical response"));

      renderBulk({ jobId, onClearJob });

      expect(await screen.findByText(safeCopy)).toBeInTheDocument();
      expect(
        screen.queryByText("raw technical response"),
      ).not.toBeInTheDocument();
      expect(
        screen.getByText(/当前系统不会自动查找其他批量任务。/),
      ).toBeInTheDocument();
      expect(requestCount(fetchSpy, `/import-jobs/${jobId}`)).toBe(1);

      fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
      await waitFor(() =>
        expect(requestCount(fetchSpy, `/import-jobs/${jobId}`)).toBe(2),
      );
      fireEvent.click(
        await screen.findByRole("button", { name: "清除当前任务" }),
      );
      expect(onClearJob).toHaveBeenCalledOnce();

      const paths = fetchSpy.mock.calls.map(([input]) => String(input));
      expect(paths.every((path) => path !== "/api/v1/import-jobs")).toBe(true);
      expect(paths.every((path) => !path.endsWith("/files"))).toBe(true);
      expect(paths.every((path) => !path.includes("/collection-jobs/"))).toBe(
        true,
      );
    },
  );

  it("rejects a successfully-read Legacy ID and does not follow it into Bulk files or Collection", async () => {
    const legacyJob = makeJob({
      id: "legacy-job",
      stored_file_id: "stored-legacy",
      original_filename: "legacy.csv",
      mime_type: "text/csv",
      file_size: 128,
      sha256: "c".repeat(64),
    });
    const onClearJob = vi.fn();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/import-jobs/legacy-job")) {
          return success(legacyJob);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderBulk({ jobId: legacyJob.id, onClearJob });

    expect(
      await screen.findByText("当前链接不是批量文件处理任务。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "系统不会把单文件任务转换为批量任务，也不会自动查找其他任务。",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除当前任务" }));
    expect(onClearJob).toHaveBeenCalledOnce();

    const paths = fetchSpy.mock.calls.map(([input]) => String(input));
    expect(paths).toEqual(["/api/v1/import-jobs/legacy-job"]);
    expect(paths).not.toContain("/api/v1/import-jobs");
    expect(paths.every((path) => !path.endsWith("/files"))).toBe(true);
    expect(paths.every((path) => !path.includes("/collection-jobs/"))).toBe(
      true,
    );
  });
});

describe("BulkImportWorkspace upload hardening", () => {
  it("starts at most two uploads, never automatically retries a failure, and keeps its client ID for manual replay", async () => {
    type DeferredUpload = {
      filename: string;
      clientFileId: string;
      promise: Promise<Response>;
      resolve: (response: Response) => void;
    };

    const job = makeJob();
    const uploads: DeferredUpload[] = [];
    let active = 0;
    let maximumActive = 0;
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input, init) => {
        const url = String(input);
        if (
          url.endsWith(`/import-jobs/${job.id}/files`) &&
          init?.method === "POST"
        ) {
          const form = init.body as FormData;
          const file = form.get("file") as File;
          const clientId = String(form.get("client_file_id"));
          active += 1;
          maximumActive = Math.max(maximumActive, active);
          let resolveResponse!: (response: Response) => void;
          const promise = new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }).finally(() => {
            active -= 1;
          });
          uploads.push({
            filename: file.name,
            clientFileId: clientId,
            promise,
            resolve: resolveResponse,
          });
          return promise;
        }
        if (url.endsWith(`/import-jobs/${job.id}/files`))
          return Promise.resolve(success([]));
        if (url.endsWith(`/import-jobs/${job.id}`))
          return Promise.resolve(success(job));
        if (url.endsWith(`/collection-jobs/${collection.id}`)) {
          return Promise.resolve(success(collection));
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      });

    renderBulk({ jobId: job.id });
    expect(await screen.findByText("还没有文件")).toBeInTheDocument();
    const selected = ["one.csv", "two.csv", "three.csv", "four.csv"].map(
      (name) => new File([name], name, { type: "text/csv" }),
    );
    fireEvent.change(screen.getByLabelText("选择批量文件"), {
      target: { files: selected },
    });

    await waitFor(() => expect(uploads).toHaveLength(2));
    expect(maximumActive).toBe(2);
    expect(requestCount(fetchSpy, `/import-jobs/${job.id}/files`, "POST")).toBe(
      2,
    );

    await act(async () => {
      uploads[0]?.resolve(
        success(
          uploadResult(
            makeFile({
              id: "file-one",
              original_filename: uploads[0]?.filename ?? "one.csv",
            }),
          ),
          201,
        ),
      );
      await uploads[0]?.promise;
    });
    await waitFor(() => expect(uploads).toHaveLength(3));
    expect(maximumActive).toBe(2);

    const failedClientId = uploads[1]?.clientFileId;
    await act(async () => {
      uploads[1]?.resolve(failure("INVALID_CSV", 422));
      await uploads[1]?.promise;
    });
    await waitFor(() => expect(uploads).toHaveLength(4));
    expect(maximumActive).toBe(2);

    await act(async () => {
      uploads[2]?.resolve(
        success(
          uploadResult(
            makeFile({ id: "file-three", original_filename: "three.csv" }),
          ),
          201,
        ),
      );
      uploads[3]?.resolve(
        success(
          uploadResult(
            makeFile({ id: "file-four", original_filename: "four.csv" }),
          ),
          201,
        ),
      );
      await Promise.all([uploads[2]?.promise, uploads[3]?.promise]);
    });

    const retryButton = await screen.findByRole("button", {
      name: "重新上传",
    });
    expect(requestCount(fetchSpy, `/import-jobs/${job.id}/files`, "POST")).toBe(
      4,
    );
    expect(maximumActive).toBe(2);
    expect(document.body.textContent).not.toMatch(/\d+%/);

    fireEvent.click(retryButton);
    await waitFor(() => expect(uploads).toHaveLength(5));
    expect(uploads[4]?.filename).toBe("two.csv");
    expect(uploads[4]?.clientFileId).toBe(failedClientId);
    expect(maximumActive).toBeLessThanOrEqual(2);

    await act(async () => {
      uploads[4]?.resolve(
        success(
          uploadResult(
            makeFile({ id: "file-two", original_filename: "two.csv" }),
          ),
          201,
        ),
      );
      await uploads[4]?.promise;
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "重新上传" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("never exposes or replays Job A's failed upload after the workspace switches to Job B", async () => {
    const jobA = makeJob({ id: "job-a" });
    const jobB = makeJob({ id: "job-b" });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (
          url.endsWith(`/import-jobs/${jobA.id}/files`) &&
          init?.method === "POST"
        ) {
          return failure("INVALID_CSV", 422);
        }
        if (
          url.endsWith(`/import-jobs/${jobB.id}/files`) &&
          init?.method === "POST"
        ) {
          return success(
            uploadResult(
              makeFile({ import_job_id: jobB.id, original_filename: "a.csv" }),
            ),
            201,
          );
        }
        if (url.endsWith(`/import-jobs/${jobA.id}/files`)) return success([]);
        if (url.endsWith(`/import-jobs/${jobB.id}/files`)) return success([]);
        if (url.endsWith(`/import-jobs/${jobA.id}`)) return success(jobA);
        if (url.endsWith(`/import-jobs/${jobB.id}`)) return success(jobB);
        if (url.endsWith(`/collection-jobs/${collection.id}`)) {
          return success(collection);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    const rendered = renderBulk({ jobId: jobA.id });
    expect(await screen.findByText("还没有文件")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("选择批量文件"), {
      target: {
        files: [new File(["invalid"], "a.csv", { type: "text/csv" })],
      },
    });
    const staleRetryButton = await screen.findByRole("button", {
      name: "重新上传",
    });
    expect(
      requestCount(fetchSpy, `/import-jobs/${jobA.id}/files`, "POST"),
    ).toBe(1);

    rendered.rerender(
      <QueryClientProvider client={rendered.queryClient}>
        <BulkImportWorkspace
          role="operator"
          jobId={jobB.id}
          onSelectJob={vi.fn()}
          onClearJob={vi.fn()}
        />
      </QueryClientProvider>,
    );
    await waitFor(() =>
      expect(
        requestCount(fetchSpy, `/import-jobs/${jobB.id}`, undefined),
      ).toBeGreaterThan(0),
    );
    expect(
      screen.queryByRole("button", { name: "重新上传" }),
    ).not.toBeInTheDocument();
    expect(document.body.contains(staleRetryButton)).toBe(false);

    fireEvent.click(staleRetryButton);
    await act(async () => {
      await Promise.resolve();
    });
    expect(
      requestCount(fetchSpy, `/import-jobs/${jobB.id}/files`, "POST"),
    ).toBe(0);
    expect(
      requestCount(fetchSpy, `/import-jobs/${jobA.id}/files`, "POST"),
    ).toBe(1);
  });
});

describe("BulkImportWorkspace file truth and actions", () => {
  it("renders every backend state, unknown versus real zero rows, safe issues, and only real actions", async () => {
    const onEditMapping = vi.fn();
    const onEditAcquisitionTime = vi.fn();
    const onRetryFile = vi.fn();
    const onExcludeFile = vi.fn();
    const statuses: ImportJobFileStatus[] = [
      "uploaded",
      "parsing",
      "mapping_required",
      "ready",
      "failed",
      "excluded",
    ];
    const files = statuses.map((status, index) =>
      makeFile({
        id: `file-${status}`,
        stored_file_id: `stored-${status}`,
        position: index + 1,
        original_filename: `${status}.csv`,
        status,
        raw_rows: status === "ready" ? 0 : index + 1,
        detected_fields: status === "uploaded" ? null : ["达人名称"],
        error_code: status === "failed" ? "UNKNOWN_WORKER_FAILURE" : null,
        error_message:
          status === "failed" ? "Traceback SQL broker secret" : null,
        excluded_at: status === "excluded" ? "2026-08-10T03:00:00Z" : null,
      }),
    );
    files[0] = makeFile({
      ...files[0],
      raw_rows: 0,
      detected_fields: null,
    });
    const props = viewProps(makeJob(), files, {
      onEditMapping,
      onEditAcquisitionTime,
      onRetryFile,
      onExcludeFile,
    });

    const view = render(<BulkImportWorkspaceView {...props} />);
    const table = await bulkFileTable(view.container);
    const uploadedRow = fileRow(table, "uploaded.csv");
    const parsingRow = fileRow(table, "parsing.csv");
    const mappingRow = fileRow(table, "mapping_required.csv");
    const readyRow = fileRow(table, "ready.csv");
    const failedRow = fileRow(table, "failed.csv");
    const excludedRow = fileRow(table, "excluded.csv");

    expect(within(uploadedRow).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(readyRow).getByText("0")).toBeInTheDocument();
    expect(within(uploadedRow).getByText("已上传")).toBeInTheDocument();
    expect(within(parsingRow).getByText("处理中")).toBeInTheDocument();
    expect(within(mappingRow).getByText("需要字段映射")).toBeInTheDocument();
    expect(within(readyRow).getByText("已就绪")).toBeInTheDocument();
    expect(within(failedRow).getByText("处理失败")).toBeInTheDocument();
    expect(within(excludedRow).getByText("已排除")).toBeInTheDocument();
    expect(
      within(failedRow).getByText("文件处理失败，请重试或排除该文件。"),
    ).toBeInTheDocument();
    expect(
      within(view.container).queryByText(/Traceback|SQL|broker|secret/),
    ).not.toBeInTheDocument();

    fireEvent.click(
      within(mappingRow).getByRole("button", {
        name: "处理字段映射",
      }),
    );
    expect(onEditMapping).toHaveBeenCalledWith(
      expect.objectContaining({ id: "file-mapping_required" }),
    );
    fireEvent.click(within(failedRow).getByRole("button", { name: "重试" }));
    expect(onRetryFile).toHaveBeenCalledWith(
      expect.objectContaining({ id: "file-failed" }),
    );
    fireEvent.click(within(readyRow).getByRole("button", { name: /更多/ }));
    fireEvent.click(await bulkMenuAction("file-ready", "修改时间"));
    expect(onEditAcquisitionTime).toHaveBeenCalledWith(
      expect.objectContaining({ id: "file-ready" }),
    );

    fireEvent.click(within(uploadedRow).getByRole("button", { name: /更多/ }));
    fireEvent.click(await bulkMenuAction("file-uploaded", "排除"));
    fireEvent.click(
      within(await exclusionConfirm()).getByRole("button", {
        name: "确认排除",
      }),
    );
    expect(onExcludeFile).toHaveBeenCalledWith(
      expect.objectContaining({ id: "file-uploaded" }),
    );
    expect(
      screen.queryByRole("button", { name: /重新纳入|取消排除|删除/ }),
    ).not.toBeInTheDocument();
    expect(within(parsingRow).queryByRole("button")).not.toBeInTheDocument();
    expect(within(excludedRow).queryByRole("button")).not.toBeInTheDocument();
  }, 10_000);

  it("PATCHes the unchanged server acquisition time to confirm it, then freezes editing after Preview", async () => {
    bulkFileTableMode.compact = true;
    const job = makeJob();
    const file = makeFile({
      source_acquired_at_confirmation_required: true,
      source_acquired_at_origin: "server_default",
    });
    const fetchSpy = recoveredRouter(job, [file], (url, init) => {
      if (
        url.endsWith(`/import-jobs/${job.id}/files/${file.id}`) &&
        init?.method === "PATCH"
      ) {
        return success(
          makeFile({
            source_acquired_at_confirmation_required: false,
            source_acquired_at_origin: "user_confirmed",
          }),
        );
      }
      return undefined;
    });

    const workspace = renderBulk({ jobId: job.id });
    const table = await bulkFileTable(workspace.container);
    const currentFileRow = fileRow(table, file.original_filename);
    expect(within(currentFileRow).getByText("待确认")).toBeInTheDocument();
    fireEvent.click(
      within(currentFileRow).getByRole("button", { name: "修改时间" }),
    );
    const timeDialog = await screen.findByRole("dialog", {
      name: "修改数据取得时间",
    });
    expect(within(timeDialog).getByLabelText("数据取得时间")).toHaveValue(
      "2026-08-10T10:00",
    );
    fireEvent.click(
      within(timeDialog).getByRole("button", { name: "保存时间" }),
    );

    await waitFor(() =>
      expect(
        requestCount(
          fetchSpy,
          `/import-jobs/${job.id}/files/${file.id}`,
          "PATCH",
        ),
      ).toBe(1),
    );
    const patchCall = fetchSpy.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith(`/import-jobs/${job.id}/files/${file.id}`) &&
        init?.method === "PATCH",
    );
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({
      source_acquired_at: "2026-08-10T10:00:00+08:00",
    });

    workspace.unmount();
    bulkFileTableMode.compact = false;
    const previewView = render(
      <BulkImportWorkspaceView
        {...viewProps(
          makeJob({ status: "preview_ready", preview_revision: 1 }),
          [makeFile()],
        )}
      />,
    );
    const previewTable = await bulkFileTable(previewView.container);
    expect(
      within(fileRow(previewTable, "ready.csv")).getByRole("button", {
        name: /更多/,
      }),
    ).toBeDisabled();
  });

  it("keeps Bulk mapping state isolated and sends Mapping, File Retry, and Exclude to their exact endpoints", async () => {
    bulkFileTableMode.compact = true;
    const job = makeJob();
    const mappingFile = makeFile({
      id: "mapping-file",
      stored_file_id: "stored-mapping",
      position: 1,
      original_filename: "mapping.csv",
      status: "mapping_required",
      detected_fields: ["达人名称", "主页链接"],
      field_mapping: null,
    });
    const failedFile = makeFile({
      id: "failed-file",
      stored_file_id: "stored-failed",
      position: 2,
      original_filename: "failed.csv",
      status: "failed",
      error_code: "INVALID_HEADER",
    });
    const readyFile = makeFile({
      id: "ready-file",
      stored_file_id: "stored-ready",
      position: 3,
      original_filename: "exclude.csv",
    });
    const fetchSpy = recoveredRouter(
      job,
      [mappingFile, failedFile, readyFile],
      (url, init) => {
        if (
          url.endsWith(`/files/${mappingFile.id}/mapping`) &&
          init?.method === "PUT"
        ) {
          return success(
            makeFile({
              ...mappingFile,
              status: "parsing",
              field_mapping: { 达人名称: "nickname" },
            }),
            202,
          );
        }
        if (
          url.endsWith(`/files/${failedFile.id}/retry`) &&
          init?.method === "POST"
        ) {
          return success(makeFile({ ...failedFile, status: "parsing" }), 202);
        }
        if (
          url.endsWith(`/files/${readyFile.id}/exclude`) &&
          init?.method === "POST"
        ) {
          return success(
            makeFile({
              ...readyFile,
              status: "excluded",
              excluded_at: "2026-08-10T04:00:00Z",
            }),
          );
        }
        return undefined;
      },
    );

    const workspace = renderBulk({ jobId: job.id });
    const table = await bulkFileTable(workspace.container);
    fireEvent.click(
      within(fileRow(table, "mapping.csv")).getByRole("button", {
        name: "处理字段映射",
      }),
    );
    const mappingDialog = await screen.findByRole("dialog", {
      name: "处理字段映射",
    });
    expect(
      within(mappingDialog).getByText(
        "需要达人官方地址 / 平台账号ID / 来源ID之一；小红书号和邮箱不能作为稳定身份字段。",
      ),
    ).toBeInTheDocument();
    const select = within(mappingDialog).getByRole("combobox", {
      name: "将 达人名称 映射到",
    });
    fireEvent.mouseDown(select);
    fireEvent.click(await mappingOption("nickname"));
    fireEvent.click(
      within(mappingDialog).getByRole("button", { name: "保存字段映射" }),
    );
    await waitFor(() =>
      expect(
        requestCount(
          fetchSpy,
          `/import-jobs/${job.id}/files/${mappingFile.id}/mapping`,
          "PUT",
        ),
      ).toBe(1),
    );
    const mappingCall = fetchSpy.mock.calls.find(([input]) =>
      String(input).endsWith(`/files/${mappingFile.id}/mapping`),
    );
    expect(JSON.parse(String(mappingCall?.[1]?.body))).toEqual({
      mapping: { 达人名称: "nickname" },
    });
    expect(String(mappingCall?.[0])).not.toContain(
      "/import-jobs/job-1/mapping",
    );

    fireEvent.click(
      within(fileRow(table, "failed.csv")).getByRole("button", {
        name: "重试",
      }),
    );
    await waitFor(() =>
      expect(
        requestCount(
          fetchSpy,
          `/import-jobs/${job.id}/files/${failedFile.id}/retry`,
          "POST",
        ),
      ).toBe(1),
    );
    fireEvent.click(
      within(fileRow(table, "exclude.csv")).getByRole("button", {
        name: "排除",
      }),
    );
    await waitFor(() =>
      expect(
        requestCount(
          fetchSpy,
          `/import-jobs/${job.id}/files/${readyFile.id}/exclude`,
          "POST",
        ),
      ).toBe(1),
    );

    const mutationPaths = fetchSpy.mock.calls
      .filter(([, init]) => init?.method && init.method !== "GET")
      .map(([input]) => String(input));
    expect(mutationPaths.every((path) => !path.includes("/confirm"))).toBe(
      true,
    );
    expect(mutationPaths.every((path) => !path.includes("unexclude"))).toBe(
      true,
    );
  }, 10_000);
});

describe("BulkImportWorkspace Preview and compatible Job states", () => {
  it("treats Preview gates as UX, sends the first request with rebuild false, and safely renders a backend conflict", async () => {
    bulkFileTableMode.compact = true;
    const job = makeJob();
    const blockedFile = makeFile({
      source_acquired_at_confirmation_required: true,
    });
    const gateProps = viewProps(job, [blockedFile]);
    const gateView = render(<BulkImportWorkspaceView {...gateProps} />);
    const gateWorkspace = within(gateView.container);
    expect(
      gateWorkspace.getByText("还有 1 个文件待确认数据取得时间。"),
    ).toBeInTheDocument();
    expect(
      gateWorkspace.getByRole("button", { name: "生成数据预览" }),
    ).toBeDisabled();

    const readyProps = viewProps(job, [makeFile()]);
    gateView.rerender(<BulkImportWorkspaceView {...readyProps} />);
    const enabledPreview = gateWorkspace.getByRole("button", {
      name: "生成数据预览",
    });
    expect(enabledPreview).toBeEnabled();
    fireEvent.click(enabledPreview);
    expect(readyProps.onRequestPreview).toHaveBeenCalledOnce();
    gateView.unmount();

    const fetchSpy = recoveredRouter(job, [makeFile()], (url, init) => {
      if (
        url.endsWith(`/import-jobs/${job.id}/preview`) &&
        init?.method === "POST"
      ) {
        return failure("INVALID_STATE_TRANSITION", 409, "raw backend conflict");
      }
      return undefined;
    });
    const workspace = renderBulk({ jobId: job.id });
    const bulkWorkspace = within(workspace.container);
    fireEvent.click(
      await bulkWorkspace.findByRole("button", { name: "生成数据预览" }),
    );
    expect(
      await bulkWorkspace.findByText("任务状态已变化，请刷新后重试。"),
    ).toBeInTheDocument();
    const previewCall = fetchSpy.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith(`/import-jobs/${job.id}/preview`) &&
        init?.method === "POST",
    );
    expect(JSON.parse(String(previewCall?.[1]?.body))).toEqual({
      rebuild: false,
    });
    expect(
      bulkWorkspace.queryByText("raw backend conflict"),
    ).not.toBeInTheDocument();
  });

  it("uses rebuild true for stale Preview and generic Job Retry for failed state", async () => {
    const staleJob = makeJob({ status: "preview_stale", preview_revision: 1 });
    let fetchSpy = recoveredRouter(staleJob, [], (url, init) => {
      if (
        url.endsWith(`/import-jobs/${staleJob.id}/preview`) &&
        init?.method === "POST"
      ) {
        return success(dispatch("previewing"), 202);
      }
      return undefined;
    });
    let rendered = renderBulk({ jobId: staleJob.id });
    fireEvent.click(
      await screen.findByRole("button", { name: "重新生成数据预览" }),
    );
    await waitFor(() =>
      expect(
        requestCount(fetchSpy, `/import-jobs/${staleJob.id}/preview`, "POST"),
      ).toBe(1),
    );
    const rebuildCall = fetchSpy.mock.calls.find(([input]) =>
      String(input).endsWith(`/import-jobs/${staleJob.id}/preview`),
    );
    expect(JSON.parse(String(rebuildCall?.[1]?.body))).toEqual({
      rebuild: true,
    });

    rendered.unmount();
    fetchSpy.mockRestore();
    const failedJob = makeJob({ status: "failed" });
    fetchSpy = recoveredRouter(failedJob, [], (url, init) => {
      if (
        url.endsWith(`/import-jobs/${failedJob.id}/retry`) &&
        init?.method === "POST"
      ) {
        return success(dispatch("parsing"), 202);
      }
      return undefined;
    });
    rendered = renderBulk({ jobId: failedJob.id });
    fireEvent.click(await screen.findByRole("button", { name: /重\s*试/ }));
    await waitFor(() =>
      expect(
        requestCount(fetchSpy, `/import-jobs/${failedJob.id}/retry`, "POST"),
      ).toBe(1),
    );
    expect(
      fetchSpy.mock.calls.some(([input]) => String(input).includes("/confirm")),
    ).toBe(false);
  });

  it.each([
    ["preview_ready", "数据预览已生成"],
    ["confirm_queued", "正在准备导入"],
    ["importing", "正在导入"],
    ["completed", "导入完成"],
  ] satisfies [ImportJobStatus, string][])(
    "renders %s as a read-only compatibility state with no Bulk Confirm or preview contents",
    (status, label) => {
      render(
        <BulkImportWorkspaceView
          {...viewProps(
            makeJob({
              status,
              preview_revision: status === "preview_ready" ? 1 : 2,
            }),
            [],
          )}
        />,
      );

      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
      expect(
        screen.queryByRole("button", { name: /确认.*预览|确认导入/ }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(
          /Preview Summary|预览摘要|筛选结果|变更摘要|分类筛选/,
        ),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(/真实导航|真实入口|真实摘要|真实操作/),
      ).not.toBeInTheDocument();
      cleanup();
    },
  );

  it("keeps Viewer controls read-only and never presents Confirm", () => {
    render(
      <BulkImportWorkspaceView
        {...viewProps(makeJob(), [makeFile()], { readOnly: true })}
      />,
    );

    expect(
      screen.getByText(
        "只读角色可以查看批量任务，但不能修改文件或生成数据预览。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成数据预览" })).toBeDisabled();
    expect(
      within(rowFor("ready.csv")).getByRole("button", { name: /更多/ }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: /确认导入/ }),
    ).not.toBeInTheDocument();
  });
});

describe("BulkImportWorkspace Buyer screening bootstrap", () => {
  it("only exposes the start action for a completed real Bulk Job and navigates to its run", async () => {
    const completed = makeJob({
      status: "completed",
      preview_revision: 1,
      confirmed_revision: 1,
      completed_at: "2026-08-10T03:00:00Z",
    });
    const fetchSpy = recoveredRouter(completed, [makeFile()], (url, init) => {
      if (
        url.endsWith(`/collection-jobs/${collection.id}/buyer-screening`) &&
        init?.method === "POST"
      ) {
        return success(
          {
            pool: { id: "buyer-pool" },
            policy: { id: "buyer-policy", version: 1 },
            run: { id: "buyer-run", status: "PENDING" },
            reused_existing_pool: false,
          },
          202,
        );
      }
      return undefined;
    });

    renderBulk({ jobId: completed.id });
    const start = await screen.findByRole("button", { name: "开始潜客筛选" });
    fireEvent.click(start);

    await waitFor(() =>
      expect(routerPush).toHaveBeenCalledWith(
        "/candidate-pools/buyer-pool/runs/buyer-run",
      ),
    );
    expect(
      requestCount(
        fetchSpy,
        `/collection-jobs/${collection.id}/buyer-screening`,
        "POST",
      ),
    ).toBe(1);

    cleanup();
    render(
      <BulkImportWorkspaceView
        {...viewProps(makeJob({ status: "preview_ready" }), [makeFile()])}
      />,
    );
    expect(
      screen.queryByRole("button", { name: "开始潜客筛选" }),
    ).not.toBeInTheDocument();
  });
});
