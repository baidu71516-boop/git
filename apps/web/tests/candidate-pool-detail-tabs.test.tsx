import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CandidatePoolDetailView } from "../src/features/candidate-pools/candidate-pool-detail-view";
import { CandidatePoolPreviewWorkspace } from "../src/features/candidate-pools/candidate-pool-preview-workspace";
import { PREVIEW_POOLS } from "../src/features/candidate-pools/preview-fixtures";

const navigation = vi.hoisted(() => ({
  tab: null as string | null,
  scene: "basic",
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/candidate-pools/pool-1",
  useRouter: () => ({ push: navigation.push, replace: navigation.replace }),
  useSearchParams: () => ({
    get: (name: string) =>
      name === "tab"
        ? navigation.tab
        : name === "scene"
          ? navigation.scene
          : null,
  }),
}));

const pool = { ...PREVIEW_POOLS[0], id: "pool-1" };

function response(data: unknown) {
  return new Response(
    JSON.stringify({ success: true, data, error: null, request_id: "test" }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

function renderDetail({
  role = "viewer",
  hasSelectedOperator = false,
}: {
  role?: "manager" | "viewer";
  hasSelectedOperator?: boolean;
} = {}) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <CandidatePoolDetailView
        poolId="pool-1"
        role={role}
        hasSelectedOperator={hasSelectedOperator}
      />
    </QueryClientProvider>,
  );
}

function mockCandidatePoolRequests() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url === "/api/v1/candidate-pools/pool-1")
      return Promise.resolve(response(pool));
    if (url.endsWith("/policies")) return Promise.resolve(response([]));
    if (url.includes("/runs?limit=50"))
      return Promise.resolve(response({ items: [], next_cursor: null }));
    throw new Error(`Unexpected request: ${url}`);
  });
}

afterEach(() => {
  navigation.tab = null;
  navigation.scene = "basic";
  navigation.push.mockReset();
  navigation.replace.mockReset();
  vi.restoreAllMocks();
});

describe("Candidate Pool detail tabs", () => {
  it.each([null, "unexpected"])(
    "falls back to basic without lazy tab requests for %s",
    async (tab) => {
      navigation.tab = tab;
      const fetchMock = mockCandidatePoolRequests();

      renderDetail();

      expect(await screen.findByText("候选池名称")).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: "基本信息" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it.each([
    ["policies", "规则历史", "/api/v1/candidate-pools/pool-1/policies"],
    ["runs", "生成记录", "/api/v1/candidate-pools/pool-1/runs?limit=50"],
  ] as const)(
    "renders %s once and only loads its lazy query",
    async (tab, label, url) => {
      navigation.tab = tab;
      const fetchMock = mockCandidatePoolRequests();

      const { rerender } = renderDetail();

      expect(
        await screen.findByRole("tabpanel", { name: label }),
      ).toBeInTheDocument();
      expect(fetchMock.mock.calls.map(([request]) => String(request))).toEqual([
        "/api/v1/candidate-pools/pool-1",
        url,
      ]);

      rerender(
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          <CandidatePoolDetailView
            poolId="pool-1"
            role="viewer"
            hasSelectedOperator={false}
          />
        </QueryClientProvider>,
      );
      await waitFor(() => expect(navigation.push).not.toHaveBeenCalled());
    },
  );

  it("navigates once for a different tab and ignores the current tab", async () => {
    const fetchMock = mockCandidatePoolRequests();
    renderDetail();

    fireEvent.click(await screen.findByRole("tab", { name: "规则历史" }));
    expect(navigation.push).toHaveBeenCalledTimes(1);
    expect(navigation.push).toHaveBeenCalledWith(
      "/candidate-pools/pool-1?tab=policies",
    );

    fireEvent.click(screen.getByRole("tab", { name: "基本信息" }));
    expect(navigation.push).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("navigates from policies to runs exactly once", async () => {
    navigation.tab = "policies";
    mockCandidatePoolRequests();
    renderDetail();

    fireEvent.click(await screen.findByRole("tab", { name: "生成记录" }));
    expect(navigation.push).toHaveBeenCalledTimes(1);
    expect(navigation.push).toHaveBeenCalledWith(
      "/candidate-pools/pool-1?tab=runs",
    );

    fireEvent.click(screen.getByRole("tab", { name: "规则历史" }));
    expect(navigation.push).toHaveBeenCalledTimes(1);
  });

  it("shows only the current Seller V1 adjustment controls in rule history", async () => {
    navigation.tab = "policies";
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url === "/api/v1/candidate-pools/pool-1")
        return Promise.resolve(response(pool));
      if (url.endsWith("/policies"))
        return Promise.resolve(
          response([
            {
              id: pool.current_policy_id,
              pool_id: "pool-1",
              version: 3,
              schema_version: 1,
              definition: {
                schema_version: 1,
                policy_type: "SELLER_V1",
                contact_availability: null,
                content_activity: null,
                long_inactivity: null,
                notes_7d: { minimum: 1 },
              },
              canonical_hash: "a".repeat(64),
              created_by_operator_id: pool.owner_operator_id,
              created_at: pool.created_at,
              updated_at: pool.updated_at,
            },
          ]),
        );
      throw new Error(`Unexpected request: ${url}`);
    });

    renderDetail({ role: "manager", hasSelectedOperator: true });

    expect(
      await screen.findByRole("button", { name: "重新运行此规则" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "调整规则并重新运行" }),
    );
    expect(
      await screen.findByText("保存新规则版本并重新运行"),
    ).toBeInTheDocument();
    expect(screen.getByText("近 7 天笔记数")).toBeInTheDocument();
    expect(screen.queryByText(/活动下降|长期未发布/)).not.toBeInTheDocument();
  });

  it.each([
    ["basic", "规则历史", "policies"],
    ["policies", "生成记录", "runs"],
    ["runs", "基本信息", "basic"],
  ] as const)(
    "maps preview %s tab changes to the existing %s scene URL",
    async (scene, targetTab, expectedScene) => {
      navigation.scene = scene;
      render(<CandidatePoolPreviewWorkspace />);

      fireEvent.click(await screen.findByRole("tab", { name: targetTab }));
      expect(navigation.replace).toHaveBeenCalledWith(
        `/dev-ui-preview/candidate-pools?scene=${expectedScene}`,
      );
    },
  );
});
