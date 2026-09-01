import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Key, ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CandidateRunDetailView } from "../src/features/candidate-pools/candidate-run-detail-view";
import { candidatePoolQueryKeys } from "../src/features/candidate-pools/queries";
import type {
  BuyerLeadTier,
  CandidateMember,
  CandidatePoolRun,
  TargetingPolicy,
} from "../src/features/candidate-pools/types";

type TestTableColumn = {
  key?: Key;
  render?: (value: unknown, record: CandidateMember) => ReactNode;
};

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    Table: ({
      columns = [],
      dataSource = [],
    }: {
      columns?: TestTableColumn[];
      dataSource?: CandidateMember[];
    }) => (
      <table>
        <tbody>
          {dataSource.map((record) => (
            <tr key={record.id}>
              {columns.map((column, index) => (
                <td key={column.key ?? index}>
                  {column.render?.(undefined, record)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    ),
    Drawer: ({
      children,
      open,
      title,
    }: {
      children?: ReactNode;
      open?: boolean;
      title?: string;
    }) => (open ? <section aria-label={title}>{children}</section> : null),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const poolId = "buyer-pool";
const runId = "buyer-run";

function member(tier: BuyerLeadTier): CandidateMember {
  return {
    id: `member-${tier}`,
    run_id: runId,
    influencer_id: `influencer-${tier}`,
    platform_account_id: `account-${tier}`,
    result: tier === "UNKNOWN" ? "UNKNOWN" : "NOT_MATCH",
    buyer_lead_tier: tier,
    buyer_relation_summary: {
      client_categories: ["原采集类目"],
      creator_categories: ["当前达人类目"],
      pairs: [
        {
          client_category_id: "原采集类目",
          creator_category_id: "当前达人类目",
          relation: tier === "HIGH" ? "INCOMPATIBLE" : "COMPATIBLE",
        },
        {
          client_category_id: "原采集类目二",
          creator_category_id: "当前达人类目二",
          relation: "PARENT_CHILD",
        },
      ],
    },
    reason_codes: [],
    redacted_evidence: { source: "HUITUN" },
    evidence_hash: `hash-${tier}`,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    influencer: {
      id: `influencer-${tier}`,
      display_name: `达人 ${tier}`,
      status: "active",
    },
    platform_account: {
      id: `account-${tier}`,
      platform: "douyin",
      platform_account_id: `account-${tier}`,
      account_name: `账号 ${tier}`,
      account_handle: tier.toLowerCase(),
      is_active: true,
    },
  };
}

function run(): CandidatePoolRun {
  return {
    id: runId,
    pool_id: poolId,
    policy_id: "buyer-policy",
    as_of: "2026-09-01T00:00:00Z",
    input_watermark: null,
    status: "COMPLETED",
    match_count: 0,
    unknown_count: 1,
    not_match_count: 4,
    error_code: null,
    error_message: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    idempotent_replay: false,
  };
}

const buyerPolicy: TargetingPolicy = {
  id: "buyer-policy",
  pool_id: poolId,
  version: 1,
  schema_version: 1,
  definition: {
    schema_version: 1,
    policy_type: "BUYER_V1",
    taxonomy: { taxonomy_version: "v1", reviewed: true },
  },
  canonical_hash: "buyer-policy-hash",
  created_by_operator_id: "operator",
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const sellerPolicy: TargetingPolicy = {
  ...buyerPolicy,
  id: "seller-policy",
  definition: { schema_version: 1, policy_type: "SELLER_V1" },
};

function response(data: unknown) {
  return new Response(
    JSON.stringify({
      success: true,
      data,
      error: null,
      request_id: "buyer-ui",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

function renderBuyer(
  summary: Record<BuyerLeadTier, CandidateMember[]>,
  legacyMembers: CandidateMember[] = [],
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(candidatePoolQueryKeys.run(poolId, runId), run());
  client.setQueryData(candidatePoolQueryKeys.policies(poolId), [buyerPolicy]);
  client.setQueryData(
    candidatePoolQueryKeys.members(poolId, runId, undefined, 50),
    {
      pages: [
        {
          items:
            legacyMembers.length > 0
              ? legacyMembers
              : Object.values(summary).flat(),
          next_cursor: null,
        },
      ],
      pageParams: [null],
    },
  );
  client.setQueryData(
    candidatePoolQueryKeys.buyerSummary(poolId, runId),
    summary,
  );
  return render(
    <QueryClientProvider client={client}>
      <CandidateRunDetailView
        hasSelectedOperator
        poolId={poolId}
        role="manager"
        runId={runId}
      />
    </QueryClientProvider>,
  );
}

function renderSeller() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(candidatePoolQueryKeys.run(poolId, runId), {
    ...run(),
    policy_id: sellerPolicy.id,
  });
  client.setQueryData(candidatePoolQueryKeys.policies(poolId), [sellerPolicy]);
  client.setQueryData(
    candidatePoolQueryKeys.members(poolId, runId, undefined, 50),
    {
      pages: [{ items: [member("HIGH")], next_cursor: null }],
      pageParams: [null],
    },
  );
  return render(
    <QueryClientProvider client={client}>
      <CandidateRunDetailView
        hasSelectedOperator
        poolId={poolId}
        role="manager"
        runId={runId}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Buyer V1 market UI", () => {
  it("shows the five-tier summary, disclaimer, market rows, and all relation pairs", async () => {
    const summary = {
      HIGH: [member("HIGH")],
      CHANGED: [member("CHANGED")],
      RELATED: [member("RELATED")],
      SAME_CATEGORY: [member("SAME_CATEGORY")],
      UNKNOWN: [member("UNKNOWN")],
    };
    renderBuyer(summary);

    for (const label of [
      "强潜客",
      "变化潜客",
      "相关潜客",
      "同类潜客",
      "待判断",
    ]) {
      expect(await screen.findAllByText(label)).not.toHaveLength(0);
    }
    expect(screen.getByText("强潜客 1")).toBeInTheDocument();
    expect(screen.getAllByText("原采集类目")).not.toHaveLength(0);
    expect(screen.getAllByText("当前达人类目")).not.toHaveLength(0);
    expect(
      screen
        .getAllByText(/^达人 (HIGH|CHANGED|RELATED|SAME_CATEGORY|UNKNOWN)$/)
        .map((item) => item.textContent),
    ).toEqual([
      "达人 HIGH",
      "达人 CHANGED",
      "达人 RELATED",
      "达人 SAME_CATEGORY",
      "达人 UNKNOWN",
    ]);
    expect(
      screen.getByText(
        "潜客等级仅用于销售线索排序，不代表已发生账号购买或交易。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("加入拓客活动")).not.toBeInTheDocument();

    expect(
      screen.getAllByRole("button", { name: "查看类目关系" }),
    ).not.toHaveLength(0);
  });

  it("filters a Buyer tier through the existing API parameter", async () => {
    const summary = {
      HIGH: [member("HIGH")],
      CHANGED: [member("CHANGED")],
      RELATED: [member("RELATED")],
      SAME_CATEGORY: [member("SAME_CATEGORY")],
      UNKNOWN: [member("UNKNOWN")],
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        response({ items: [member("HIGH")], next_cursor: null }),
      );
    renderBuyer(summary);

    fireEvent.click(await screen.findByRole("tab", { name: "强潜客" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "buyer_lead_tier=HIGH",
    );
  });

  it("shows the historical-run limitation instead of inventing Buyer tier data", async () => {
    const historical = {
      ...member("UNKNOWN"),
      buyer_lead_tier: null,
      buyer_relation_summary: null,
    };
    renderBuyer(
      {
        HIGH: [],
        CHANGED: [],
        RELATED: [],
        SAME_CATEGORY: [],
        UNKNOWN: [],
      },
      [historical],
    );
    expect(
      await screen.findByText(
        "该历史运行创建于潜客分层上线前，部分同类/相关线索未保存。",
      ),
    ).toBeInTheDocument();
  });

  it("keeps Buyer tier filters out of Seller candidate runs", async () => {
    renderSeller();
    await screen.findByRole("tab", { name: "符合条件" });
    expect(
      screen.queryByRole("tab", { name: "强潜客" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "潜客等级仅用于销售线索排序，不代表已发生账号购买或交易。",
      ),
    ).not.toBeInTheDocument();
  });
});
