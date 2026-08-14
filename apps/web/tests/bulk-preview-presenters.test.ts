import {
  formatPreviewValue,
  getPreviewActionPresentation,
  getPreviewCategoryLabel,
  getScreeningPresentation,
  presentChangeSummary,
  presentPreviewIssues,
  presentPreviewRow,
  presentScreening,
  type PreviewRowPresentationInput,
} from "@/features/imports/preview-presenters";
import type {
  ImportRowAction,
  ImportRowCategory,
  ScreeningResult,
} from "@/features/imports/types";

function rowInput(
  overrides: Partial<PreviewRowPresentationInput> = {},
): PreviewRowPresentationInput {
  return {
    action: "update",
    normalized_data: { display_name: "小美" },
    merge_plan: {},
    warnings: [],
    errors: [],
    ...overrides,
  };
}

describe("unified Preview presentation mappings", () => {
  it("maps every backend action to the frozen employee wording", () => {
    const expected: Array<[ImportRowAction, string]> = [
      ["create", "新增"],
      ["update", "更新"],
      ["no_change", "无需变更"],
      ["skip", "跳过"],
      ["error", "错误"],
      ["manual_review", "需人工处理"],
    ];

    for (const [value, label] of expected) {
      expect(getPreviewActionPresentation(value).label).toBe(label);
    }
    expect(getPreviewActionPresentation("worker_state")).toEqual({
      label: "未知",
      tone: "default",
    });
    expect(getPreviewActionPresentation("toString")).toEqual({
      label: "未知",
      tone: "default",
    });
  });

  it("maps every screening result to Chinese text", () => {
    const expected: Array<[ScreeningResult, string]> = [
      ["MATCH", "符合条件"],
      ["NOT_MATCH", "不符合条件"],
      ["UNKNOWN", "信息不足"],
    ];

    for (const [value, label] of expected) {
      expect(getScreeningPresentation(value)?.label).toBe(label);
    }
    expect(getScreeningPresentation("MAYBE")).toBeNull();
  });

  it("maps every category without exposing the raw enum", () => {
    const expected: Array<[ImportRowCategory, string]> = [
      ["all", "全部"],
      ["attention", "需关注"],
      ["error", "错误"],
      ["manual_review", "人工处理"],
      ["warning", "有警告"],
      ["changed", "有变更"],
      ["new", "新增"],
      ["no_change", "无需变更"],
      ["duplicate", "重复"],
    ];

    for (const [value, label] of expected) {
      expect(getPreviewCategoryLabel(value)).toBe(label);
    }
    expect(getPreviewCategoryLabel("constructor")).toBe("全部");
  });
});

describe("screening presentation", () => {
  it("returns only safe, readable overall and evidence fields", () => {
    const result = presentScreening({
      result: "MATCH",
      rule_schema_version: 1,
      rule_revision: 4,
      rule_hash: "a".repeat(64),
      token: "worker-token",
      evidence: [
        {
          rule: "platforms",
          result: "MATCH",
          configured: ["xiaohongshu"],
          observed: "xiaohongshu",
          reason: "PLATFORM_MATCH",
          raw: "do-not-return",
        },
        {
          rule: "source_tags_exact_any",
          result: "MATCH",
          configured: ["美妆", "mail private@example.com"],
          observed: ["美妆", "电话 13800138000"],
          reason: "SOURCE_TAG_MATCH",
        },
      ],
    });

    expect(result).toMatchObject({
      result: "MATCH",
      label: "符合条件",
      evidence: [
        {
          label: "平台",
          configured: "小红书",
          observed: "小红书",
        },
        {
          label: "达人标签",
          configured: "美妆、mail ***",
          observed: "美妆、电话 ***",
        },
      ],
    });
    const serialized = JSON.stringify(result);
    expect(serialized).not.toContain("private@example.com");
    expect(serialized).not.toContain("13800138000");
    expect(serialized).not.toContain("worker-token");
    expect(serialized).not.toContain("do-not-return");
    expect(serialized).not.toContain("rule_hash");
  });

  it("drops malformed evidence and rejects a malformed overall result", () => {
    expect(presentScreening(null)).toBeNull();
    expect(presentScreening({ result: "MAYBE", evidence: [] })).toBeNull();
    expect(
      presentScreening({
        result: "UNKNOWN",
        evidence: [
          null,
          "bad",
          { rule: "private_rule", result: "MATCH" },
          { rule: "toString", result: "MATCH" },
        ],
      }),
    ).toMatchObject({ label: "信息不足", evidence: [] });
  });
});

describe("Change Summary presentation", () => {
  it("presents the exact four buckets, Chinese fields, and formatted values", () => {
    const result = presentChangeSummary({
      effective_changes: [
        {
          scope: "current_metrics",
          field: "followers_count",
          before: 182_000,
          incoming: 206_600,
          after: 206_600,
          effect: "apply",
          reason: "CURRENT_METRICS_APPLIED",
          added: [],
          removed: [],
        },
        {
          scope: "account",
          field: "source_tags",
          before: null,
          incoming: null,
          after: null,
          effect: "apply",
          reason: "ACCOUNT_FIELD_APPLIED",
          added: ["生活方式"],
          removed: ["旧标签"],
        },
      ],
      ignored_changes: [
        {
          scope: "source_state",
          field: "bio",
          before: "原简介",
          incoming: "较早的简介",
          after: "原简介",
          effect: "ignore",
          reason: "SOURCE_VALUE_RETAINED",
          added: [],
          removed: [],
        },
      ],
      freshness_changes: [
        {
          scope: "freshness",
          field: "source_acquired_at",
          before: "2026-08-01T00:00:00Z",
          incoming: "2026-08-10T00:00:00Z",
          after: "2026-08-10T00:00:00Z",
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
          reason: "METRIC_SNAPSHOT_CREATED",
          added: [],
          removed: [],
        },
      ],
    });

    expect(result.map(({ key, label }) => [key, label])).toEqual([
      ["effective_changes", "本次会更新"],
      ["ignored_changes", "不会更新"],
      ["freshness_changes", "数据更新时间变化"],
      ["historical_observations", "历史观察"],
    ]);
    expect(result[0]?.items[0]).toMatchObject({
      label: "粉丝数",
      before: "18.2万",
      incoming: "20.66万",
      after: "20.66万",
    });
    expect(result[0]?.items[1]).toMatchObject({
      label: "达人标签",
      added: ["生活方式"],
      removed: ["旧标签"],
    });
    expect(result[1]?.items[0]).toMatchObject({
      label: "简介",
      incoming: "较早的简介",
      after: "原简介",
    });
    expect(result[2]?.items[0]?.label).toBe("数据取得时间");
    expect(result[3]?.items[0]).toMatchObject({
      label: "指标快照",
      incoming: "粉丝数：20.66万",
    });
  });

  it("never returns Contact values or arbitrary extra Contact fields", () => {
    const result = presentChangeSummary({
      effective_changes: [
        {
          scope: "contact",
          contact_type: "email",
          operation: "create",
          validation_status: "valid",
          count: 2,
          possible_duplicate: true,
          value: "private@example.com",
          normalized_value: "private@example.com",
          phone: "13800138000",
          token: "contact-task-token",
        },
      ],
    });

    expect(result[0]?.items).toEqual([
      {
        kind: "contact",
        label: "联系邮箱",
        before: null,
        incoming: null,
        after: null,
        added: [],
        removed: [],
        message: "新增 2 条，其中包含疑似重复联系方式",
      },
    ]);
    const serialized = JSON.stringify(result);
    expect(serialized).not.toContain("private@example.com");
    expect(serialized).not.toContain("13800138000");
    expect(serialized).not.toContain("contact-task-token");
    expect(serialized).not.toContain("normalized_value");
  });

  it("redacts embedded email and phone values a second time", () => {
    const result = presentChangeSummary({
      effective_changes: [
        {
          scope: "account",
          field: "bio",
          before: "邮箱 old@example.com",
          incoming: "商务电话 13800138000",
          after: "联系 new@example.com 或 +86 139 0013 9000",
          added: ["private@example.com", "电话 13700137000"],
          removed: [],
        },
        {
          scope: "metric_snapshot",
          field: "metrics",
          before: null,
          incoming: {
            followers_count: 100,
            note: "联系 hidden@example.com",
            phone: "13600136000",
          },
          after: null,
          added: [],
          removed: [],
        },
      ],
    });
    const serialized = JSON.stringify(result);

    for (const secret of [
      "old@example.com",
      "new@example.com",
      "private@example.com",
      "hidden@example.com",
      "13800138000",
      "139 0013 9000",
      "13700137000",
      "13600136000",
    ]) {
      expect(serialized).not.toContain(secret);
    }
    expect(serialized).toContain("***");
  });

  it("returns four empty safe buckets for missing or malformed documents", () => {
    for (const value of [null, "bad", [], { effective_changes: "bad" }]) {
      const result = presentChangeSummary(value);
      expect(result).toHaveLength(4);
      expect(result.every((bucket) => bucket.items.length === 0)).toBe(true);
    }
  });
});

describe("row and Issue whitelisting", () => {
  it("reads only a string display_name and safely parses row detail fields", () => {
    const result = presentPreviewRow(
      rowInput({
        action: "skip",
        normalized_data: {
          display_name: "达人 private@example.com 13800138000",
          contacts: ["never-return@example.com"],
          raw_storage_locator: "/srv/private.csv",
        },
        merge_plan: {
          screening: {
            result: "NOT_MATCH",
            evidence: [],
          } as never,
          batch_duplicate: {
            owner_file_position: 2,
            owner_row_number: 7,
            owner_import_row_id: "internal-row-id",
            group_id: "group-secret",
            hard_identity_keys: ["email:never-return@example.com"],
          },
          change_summary: {
            effective_changes: [],
            ignored_changes: [],
            freshness_changes: [],
            historical_observations: [],
          },
          preview_context_hash: "context-hash-secret",
          contacts: { value: "contact@example.com" },
          token: "broker-token",
        },
      }),
    );

    expect(result).toMatchObject({
      displayName: "达人 *** ***",
      action: { label: "跳过" },
      screening: { label: "不符合条件" },
      batchDuplicate: { ownerFilePosition: 2, ownerRowNumber: 7 },
      isBatchDuplicate: true,
      changeCount: 0,
      hasChanges: false,
      issueCount: 0,
    });
    const serialized = JSON.stringify(result);
    for (const secret of [
      "private@example.com",
      "13800138000",
      "never-return@example.com",
      "/srv/private.csv",
      "internal-row-id",
      "group-secret",
      "context-hash-secret",
      "contact@example.com",
      "broker-token",
    ]) {
      expect(serialized).not.toContain(secret);
    }
  });

  it("treats missing and malformed nested row data as empty", () => {
    const malformed = presentPreviewRow({
      action: "worker_internal",
      normalized_data: { display_name: 123, nickname: "not-a-fallback" },
      merge_plan: {
        screening: { result: "MAYBE", evidence: "bad" },
        change_summary: { effective_changes: [{ field: 12 }] },
        batch_duplicate: { owner_row_number: "2", token: "hidden" },
      },
      warnings: "not-an-array",
      errors: { traceback: "not-an-array" },
    } as unknown as PreviewRowPresentationInput);

    expect(malformed).toMatchObject({
      displayName: null,
      action: { label: "未知" },
      screening: null,
      batchDuplicate: null,
      isBatchDuplicate: false,
      changeCount: 0,
      issues: [],
    });
  });

  it("uses safe employee Issue copy and never forwards dumps or contacts", () => {
    const issues = presentPreviewIssues(
      [
        {
          code: "INVALID_EMAIL",
          message: "raw private@example.com",
          field: "email",
          traceback: "/srv/worker.py:20",
        },
        {
          code: "BATCH_DATABASE_IDENTITY_CONFLICT",
          message: "raw identity graph candidate ids",
        },
        {
          code: "BROKER_SQL_TRACE",
          message: "SELECT * FROM contacts; token=secret; phone=13800138000",
          field: "/srv/model.py",
        },
      ],
      [
        {
          code: "MISSING_PLATFORM_IDENTITY",
          message: "raw HTTP dump",
          field: "profile_url",
          details: { authorization: "Bearer secret" },
        },
      ],
    );

    expect(issues).toEqual([
      {
        code: "MISSING_PLATFORM_IDENTITY",
        message: "缺少可识别的达人账号信息。",
        field: "达人主页",
        severity: "error",
      },
      {
        code: "INVALID_EMAIL",
        message: "联系邮箱格式无效，不会作为正式联系方式保存。",
        field: "联系邮箱",
        severity: "warning",
      },
      {
        code: "BATCH_DATABASE_IDENTITY_CONFLICT",
        message: "同批次达人数据对应到多个现有账号，需要人工处理。",
        field: null,
        severity: "warning",
      },
      {
        code: null,
        message: "该行存在需要关注的问题。",
        field: null,
        severity: "warning",
      },
    ]);
    const serialized = JSON.stringify(issues);
    for (const secret of [
      "private@example.com",
      "/srv/worker.py",
      "SELECT *",
      "13800138000",
      "raw HTTP dump",
      "Bearer secret",
      "BROKER_SQL_TRACE",
      "raw identity graph candidate ids",
    ]) {
      expect(serialized).not.toContain(secret);
    }
  });

  it("keeps decimal strings and ISO dates while formatting safe values", () => {
    expect(formatPreviewValue("98.123456789")).toBe("98.123456789");
    expect(formatPreviewValue("2026-08-10T10:00:00+08:00")).toBe(
      "2026-08-10 10:00",
    );
  });
});
