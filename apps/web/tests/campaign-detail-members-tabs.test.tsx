import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CampaignDetailView } from "../src/features/campaigns/campaign-detail-view";

const navigation = vi.hoisted(() => ({
  tab: null as string | null,
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/campaigns/campaign-1",
  useRouter: () => ({ push: navigation.push }),
  useSearchParams: () => ({
    get: (name: string) => (name === "tab" ? navigation.tab : null),
  }),
}));

function response(data: unknown) {
  return new Response(
    JSON.stringify({ success: true, data, error: null, request_id: "test" }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

const campaign = {
  id: "campaign-1",
  department_id: "dept-1",
  owner_operator_id: "operator-1",
  owner: { id: "operator-1", name: "王小明", status: "active" as const },
  created_by_operator_id: "operator-1",
  name: "秋季新品拓展",
  status: "ACTIVE",
  review_mode: "FIRST_N",
  review_count: 50,
  duplicate_history_policy: "ALLOW_WITH_WARNING",
  duplicate_window_days: null,
  version: 1,
  created_at: "2026-08-20T01:00:00Z",
  updated_at: "2026-08-20T02:00:00Z",
};

function renderDetail() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <CampaignDetailView
        campaignId="campaign-1"
        role="viewer"
        hasSelectedOperator={false}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  navigation.tab = null;
  navigation.push.mockReset();
  vi.restoreAllMocks();
});

describe("Campaign Detail member tabs", () => {
  it("defaults invalid or missing tab state to 基本信息 without requesting members", async () => {
    navigation.tab = "unexpected";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response(campaign));

    renderDetail();

    expect(await screen.findByText("活动名称")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/campaigns/campaign-1",
    );
  });

  it("uses ?tab=members and lazily loads the member list", async () => {
    navigation.tab = "members";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(campaign))
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }));

    renderDetail();

    expect(await screen.findByText("暂无活动达人")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      "/api/v1/campaigns/campaign-1/members?limit=50",
    );
  });
});
