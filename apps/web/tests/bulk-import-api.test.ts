import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";

import { ApiClientError } from "@/lib/api/client";

import {
  bulkImportApiPaths,
  createBulkImportJob,
  createCollectionJob,
  excludeBulkImportFile,
  getCollectionJob,
  getImportJob,
  listBulkImportFiles,
  listCollectionJobs,
  requestBulkPreview,
  retryBulkImportFile,
  retryBulkImportJob,
  updateBulkImportFileMapping,
  updateBulkImportFileSourceAcquiredAt,
  uploadBulkImportFile,
} from "@/features/imports/api";
import {
  AMBIGUOUS_BULK_CREATE_MESSAGE,
  formatBulkDateTime,
  formatBulkFileAcquisitionTime,
  formatBulkFileRowCount,
  getBulkErrorMessage,
  getBulkFileIssueMessage,
  getBulkFileIssueTone,
  getBulkFileStatusPresentation,
  getBulkJobStatusPresentation,
  getPreviewGate,
  isAmbiguousBulkCreateError,
} from "@/features/imports/formatters";
import {
  isActiveJobStatus,
  retryBulkRead,
  shouldPollFiles,
  useCreateBulkImportJobMutation,
  useCreateCollectionJobMutation,
  useExcludeBulkImportFileMutation,
  useRequestBulkPreviewMutation,
  useRetryBulkImportFileMutation,
  useRetryBulkImportJobMutation,
  useUpdateBulkImportFileMappingMutation,
  useUpdateBulkImportFileSourceAcquiredAtMutation,
  useUploadBulkImportFileMutation,
} from "@/features/imports/queries";
import type { ImportJobFilePublic } from "@/features/imports/types";

const { apiRequestMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
}));

vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/client")>();
  return { ...actual, apiRequest: apiRequestMock };
});

function success(data: unknown) {
  return {
    success: true,
    data,
    error: null,
    request_id: "request-1",
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
    original_filename: "creators.csv",
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

beforeEach(() => {
  apiRequestMock.mockReset();
});

describe("Bulk import API contract", () => {
  it("centralizes and URL-encodes every resource path", () => {
    expect(bulkImportApiPaths.collectionJob("collection/id")).toBe(
      "/collection-jobs/collection%2Fid",
    );
    expect(bulkImportApiPaths.importJob("job/id")).toBe(
      "/import-jobs/job%2Fid",
    );
    expect(bulkImportApiPaths.file("job/id", "file id")).toBe(
      "/import-jobs/job%2Fid/files/file%20id",
    );
    expect(bulkImportApiPaths.fileMapping("job/id", "file id")).toBe(
      "/import-jobs/job%2Fid/files/file%20id/mapping",
    );
    expect(bulkImportApiPaths.fileRetry("job/id", "file id")).toBe(
      "/import-jobs/job%2Fid/files/file%20id/retry",
    );
    expect(bulkImportApiPaths.fileExclude("job/id", "file id")).toBe(
      "/import-jobs/job%2Fid/files/file%20id/exclude",
    );
  });

  it("uses the exact Collection and Bulk create/read requests", async () => {
    const collectionPayload = {
      name: "美妆达人采集",
      industry: "美妆",
      purpose: "商务开发",
      target_action: "建立联系",
      target_count: 100,
      source_type: "manual_huitun_export" as const,
    };
    apiRequestMock
      .mockResolvedValueOnce(success([]))
      .mockResolvedValueOnce(success({ id: "collection-1" }))
      .mockResolvedValueOnce(success({ id: "collection-1" }))
      .mockResolvedValueOnce(success({ id: "job-1" }))
      .mockResolvedValueOnce(success({ id: "job-1" }))
      .mockResolvedValueOnce(success([]));

    await listCollectionJobs();
    await getCollectionJob("collection-1");
    await createCollectionJob(collectionPayload);
    await createBulkImportJob({ collection_job_id: "collection-1" });
    await getImportJob("job-1");
    await listBulkImportFiles("job-1");

    expect(apiRequestMock.mock.calls).toEqual([
      ["/collection-jobs"],
      ["/collection-jobs/collection-1"],
      [
        "/collection-jobs",
        { method: "POST", body: JSON.stringify(collectionPayload) },
      ],
      [
        "/import-jobs/bulk",
        {
          method: "POST",
          body: JSON.stringify({ collection_job_id: "collection-1" }),
        },
      ],
      ["/import-jobs/job-1"],
      ["/import-jobs/job-1/files"],
    ]);
  });

  it("uploads one multipart file with exact fields and omits an unset time", async () => {
    const file = new File(["达人名称\n小美"], "creators.csv", {
      type: "text/csv",
    });
    apiRequestMock.mockResolvedValue(
      success({ file: makeFile(), idempotent: false }),
    );

    await uploadBulkImportFile({
      importJobId: "job-1",
      clientFileId: "client-file-1",
      file,
    });

    const [path, init] = apiRequestMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/import-jobs/job-1/files");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    expect(form.get("client_file_id")).toBe("client-file-1");
    expect(form.get("file")).toBe(file);
    expect(form.has("source_acquired_at")).toBe(false);
    expect(init.headers).toBeUndefined();
  });

  it("includes source_acquired_at only when explicitly provided", async () => {
    const file = new File(["达人名称\n小美"], "creators.csv", {
      type: "text/csv",
    });
    apiRequestMock.mockResolvedValue(
      success({ file: makeFile(), idempotent: false }),
    );

    await uploadBulkImportFile({
      importJobId: "job-1",
      clientFileId: "client-file-1",
      file,
      sourceAcquiredAt: "2026-08-10T10:00:00+08:00",
    });

    const init = apiRequestMock.mock.calls[0]?.[1] as RequestInit;
    const form = init.body as FormData;
    expect(form.get("source_acquired_at")).toBe("2026-08-10T10:00:00+08:00");
  });

  it("uses exact per-file mutation methods, paths, and bodies", async () => {
    apiRequestMock.mockResolvedValue(success(makeFile()));

    await updateBulkImportFileSourceAcquiredAt({
      importJobId: "job-1",
      importJobFileId: "file-1",
      sourceAcquiredAt: "2026-08-10T10:00:00+08:00",
    });
    await updateBulkImportFileMapping({
      importJobId: "job-1",
      importJobFileId: "file-1",
      mapping: { 达人名称: "nickname" },
    });
    await retryBulkImportFile({
      importJobId: "job-1",
      importJobFileId: "file-1",
    });
    await excludeBulkImportFile({
      importJobId: "job-1",
      importJobFileId: "file-1",
    });

    expect(apiRequestMock.mock.calls).toEqual([
      [
        "/import-jobs/job-1/files/file-1",
        {
          method: "PATCH",
          body: JSON.stringify({
            source_acquired_at: "2026-08-10T10:00:00+08:00",
          }),
        },
      ],
      [
        "/import-jobs/job-1/files/file-1/mapping",
        {
          method: "PUT",
          body: JSON.stringify({ mapping: { 达人名称: "nickname" } }),
        },
      ],
      ["/import-jobs/job-1/files/file-1/retry", { method: "POST" }],
      ["/import-jobs/job-1/files/file-1/exclude", { method: "POST" }],
    ]);
  });

  it("separates first Preview, stale rebuild, and generic Job retry", async () => {
    apiRequestMock.mockResolvedValue(
      success({
        import_job_id: "job-1",
        status: "previewing",
        preview_revision: 0,
        task_id: "task-1",
        idempotent: false,
      }),
    );

    await requestBulkPreview({ importJobId: "job-1", rebuild: false });
    await requestBulkPreview({ importJobId: "job-1", rebuild: true });
    await retryBulkImportJob("job-1");

    expect(apiRequestMock.mock.calls).toEqual([
      [
        "/import-jobs/job-1/preview",
        { method: "POST", body: JSON.stringify({ rebuild: false }) },
      ],
      [
        "/import-jobs/job-1/preview",
        { method: "POST", body: JSON.stringify({ rebuild: true }) },
      ],
      ["/import-jobs/job-1/retry", { method: "POST" }],
    ]);
    const paths = apiRequestMock.mock.calls.map(([path]) => String(path));
    expect(paths).not.toContain("/import-jobs");
    expect(paths.every((path) => !path.includes("/confirm"))).toBe(true);
    expect(paths.every((path) => !path.includes("unexclude"))).toBe(true);
    expect(paths.every((path) => !path.includes("/tasks"))).toBe(true);
  });

  it("rejects a successful envelope that lacks data", async () => {
    apiRequestMock.mockResolvedValue(success(null));
    await expect(getImportJob("job-1")).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 200,
    });
  });
});

describe("Bulk query retry and polling policy", () => {
  it("retries a read at most once, except client errors below 500", () => {
    expect(
      retryBulkRead(0, new ApiClientError("not found", 404, "NOT_FOUND")),
    ).toBe(false);
    expect(
      retryBulkRead(0, new ApiClientError("server", 500, "INTERNAL_ERROR")),
    ).toBe(true);
    expect(
      retryBulkRead(1, new ApiClientError("server", 500, "INTERNAL_ERROR")),
    ).toBe(false);
    expect(retryBulkRead(0, new Error("network"))).toBe(true);
    expect(retryBulkRead(1, new Error("network"))).toBe(false);
  });

  it("polls only real active Job and File states", () => {
    expect(isActiveJobStatus("previewing")).toBe(true);
    expect(isActiveJobStatus("confirm_queued")).toBe(true);
    expect(isActiveJobStatus("importing")).toBe(true);
    for (const idle of [
      "draft",
      "preview_ready",
      "preview_stale",
      "failed",
      "completed",
      "cancelled",
    ] as const) {
      expect(isActiveJobStatus(idle)).toBe(false);
    }
    expect(shouldPollFiles([makeFile({ status: "uploaded" })])).toBe(true);
    expect(shouldPollFiles([makeFile({ status: "parsing" })])).toBe(true);
    expect(shouldPollFiles([makeFile({ status: "ready" })])).toBe(false);
    expect(shouldPollFiles(undefined)).toBe(false);
  });

  it("disables automatic retry for every Bulk mutation", async () => {
    apiRequestMock.mockRejectedValue(new Error("network result unknown"));
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: 2 } },
    });
    function Wrapper({ children }: { children: ReactNode }) {
      return createElement(
        QueryClientProvider,
        { client: queryClient },
        children,
      );
    }
    const { result } = renderHook(
      () => ({
        createCollection: useCreateCollectionJobMutation(),
        createBulk: useCreateBulkImportJobMutation(),
        upload: useUploadBulkImportFileMutation(),
        updateTime: useUpdateBulkImportFileSourceAcquiredAtMutation(),
        updateMapping: useUpdateBulkImportFileMappingMutation(),
        retryFile: useRetryBulkImportFileMutation(),
        excludeFile: useExcludeBulkImportFileMutation(),
        preview: useRequestBulkPreviewMutation(),
        retryJob: useRetryBulkImportJobMutation(),
      }),
      { wrapper: Wrapper },
    );
    const file = new File(["content"], "creators.csv", { type: "text/csv" });
    const actions: Array<() => Promise<unknown>> = [
      () =>
        result.current.createCollection.mutateAsync({
          name: "任务",
          industry: "美妆",
          purpose: "开发",
          target_action: "联系",
          target_count: 10,
        }),
      () =>
        result.current.createBulk.mutateAsync({
          collection_job_id: "collection-1",
        }),
      () =>
        result.current.upload.mutateAsync({
          importJobId: "job-1",
          clientFileId: "stable-client-id",
          file,
        }),
      () =>
        result.current.updateTime.mutateAsync({
          importJobId: "job-1",
          importJobFileId: "file-1",
          sourceAcquiredAt: "2026-08-10T10:00:00+08:00",
        }),
      () =>
        result.current.updateMapping.mutateAsync({
          importJobId: "job-1",
          importJobFileId: "file-1",
          mapping: { 达人名称: "nickname" },
        }),
      () =>
        result.current.retryFile.mutateAsync({
          importJobId: "job-1",
          importJobFileId: "file-1",
        }),
      () =>
        result.current.excludeFile.mutateAsync({
          importJobId: "job-1",
          importJobFileId: "file-1",
        }),
      () =>
        result.current.preview.mutateAsync({
          importJobId: "job-1",
          rebuild: false,
        }),
      () => result.current.retryJob.mutateAsync("job-1"),
    ];

    for (const action of actions) {
      await act(async () => {
        await expect(action()).rejects.toThrow("network result unknown");
      });
    }

    expect(apiRequestMock).toHaveBeenCalledTimes(actions.length);
    queryClient.clear();
  });
});

describe("Bulk import presentation truth", () => {
  it("translates every required Job and File state", () => {
    expect(getBulkFileStatusPresentation("uploaded").label).toBe("已上传");
    expect(getBulkFileStatusPresentation("parsing").label).toBe("处理中");
    expect(getBulkFileStatusPresentation("ready").label).toBe("已就绪");
    expect(getBulkFileStatusPresentation("mapping_required").label).toBe(
      "需要字段映射",
    );
    expect(getBulkFileStatusPresentation("failed").label).toBe("处理失败");
    expect(getBulkFileStatusPresentation("excluded").label).toBe("已排除");
    expect(getBulkJobStatusPresentation("previewing").label).toBe(
      "正在生成数据预览",
    );
    expect(getBulkJobStatusPresentation("preview_ready").label).toBe(
      "数据预览已生成",
    );
    expect(getBulkJobStatusPresentation("preview_stale").label).toBe(
      "数据预览需要重新生成",
    );
    expect(getBulkJobStatusPresentation("confirm_queued").label).toBe(
      "正在准备导入",
    );
    expect(getBulkJobStatusPresentation("importing").label).toBe("正在导入");
    expect(getBulkJobStatusPresentation("completed").label).toBe("导入完成");
  });

  it("distinguishes provisional zero rows from a parsed real zero", () => {
    expect(
      formatBulkFileRowCount(makeFile({ raw_rows: 0, detected_fields: null })),
    ).toBe("—");
    expect(
      formatBulkFileRowCount(makeFile({ raw_rows: 0, detected_fields: [] })),
    ).toBe("0");
    expect(formatBulkFileRowCount(makeFile({ raw_rows: 1_234 }))).toBe("1,234");
  });

  it("formats acquisition time without exposing its origin enum", () => {
    expect(formatBulkDateTime("2026-08-10T10:00:00+08:00")).toBe(
      "2026-08-10 10:00",
    );
    expect(formatBulkDateTime("invalid")).toBe("—");
    expect(
      formatBulkFileAcquisitionTime(
        makeFile({ source_acquired_at_confirmation_required: true }),
      ),
    ).toBe("待确认");
  });

  it("uses safe file and API error copy instead of raw backend messages", () => {
    const file = makeFile({
      status: "failed",
      error_code: "SYNTHETIC_UNKNOWN",
      error_message: "postgres password=secret traceback",
    });
    expect(getBulkFileIssueMessage(file)).toBe(
      "文件处理失败，请重试或排除该文件。",
    );
    const apiError = new ApiClientError(
      "raw worker traceback",
      409,
      "IMPORT_BATCH_FROZEN",
    );
    expect(getBulkErrorMessage(apiError)).toBe(
      "已生成过数据预览，当前批次不能再修改文件。",
    );
    expect(getBulkErrorMessage(new Error("socket secret"), "读取失败")).toBe(
      "读取失败",
    );
    expect(
      getBulkFileIssueTone(makeFile({ status: "ready", warning_rows: 0 })),
    ).toBe("secondary");
    expect(
      getBulkFileIssueTone(
        makeFile({
          status: "ready",
          error_rows: 0,
          warning_rows: 3,
        }),
      ),
    ).toBe("warning");
    expect(getBulkFileIssueTone(makeFile({ status: "failed" }))).toBe("danger");
  });

  it("identifies ambiguous Bulk creation without retrying it", () => {
    expect(isAmbiguousBulkCreateError(new Error("network"))).toBe(true);
    expect(
      isAmbiguousBulkCreateError(
        new ApiClientError("service", 503, "INTERNAL_ERROR"),
      ),
    ).toBe(true);
    expect(
      isAmbiguousBulkCreateError(
        new ApiClientError("missing result", 201, "INVALID_RESPONSE"),
      ),
    ).toBe(true);
    expect(
      isAmbiguousBulkCreateError(
        new ApiClientError("inactive", 409, "COLLECTION_JOB_NOT_ACTIVE"),
      ),
    ).toBe(false);
    expect(AMBIGUOUS_BULK_CREATE_MESSAGE).toContain("系统不会自动重试");
  });

  it("computes the UX Preview gate from last real File data", () => {
    expect(getPreviewGate([])).toEqual({
      allowed: false,
      reason: "请先添加至少一个可用文件。",
    });
    expect(getPreviewGate([makeFile({ status: "failed" })]).reason).toContain(
      "有",
    );
    expect(
      getPreviewGate([makeFile({ status: "mapping_required" })]).reason,
    ).toContain("需要处理字段映射");
    expect(getPreviewGate([makeFile({ status: "parsing" })]).reason).toContain(
      "正在处理",
    );
    expect(
      getPreviewGate([
        makeFile({ source_acquired_at_confirmation_required: true }),
      ]).reason,
    ).toContain("待确认数据取得时间");
    expect(getPreviewGate([makeFile()])).toEqual({
      allowed: true,
      reason: "文件已就绪，可以生成数据预览。",
    });
    expect(getPreviewGate([makeFile({ status: "excluded" })]).allowed).toBe(
      false,
    );
  });
});
