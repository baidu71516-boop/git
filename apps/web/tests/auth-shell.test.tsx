import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { AuthShell } from "../src/components/auth-shell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/influencers",
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(() => {
  vi.restoreAllMocks();
});

function response(data: unknown) {
  return new Response(
    JSON.stringify({
      success: true,
      data,
      error: null,
      request_id: "test",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

function renderAuthenticatedShell(
  workspace: "imports" | "influencers" | "refresh-queues",
  influencerId?: string,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthShell workspace={workspace} influencerId={influencerId} />
    </QueryClientProvider>,
  );
}

describe("AuthShell", () => {
  it("renders the Department login form for an unauthenticated user", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            success: false,
            data: null,
            error: {
              code: "AUTH_REQUIRED",
              message: "Authentication required",
              details: null,
            },
            request_id: "test",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            success: true,
            data: [{ id: "department-1", name: "商务部", status: "active" }],
            error: null,
            request_id: "test",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );

    render(<AuthShell />);

    await waitFor(() =>
      expect(screen.getByText("请使用部门密码登录。")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("部门")).toBeInTheDocument();
    expect(screen.getByLabelText("密码")).toBeInTheDocument();
    expect(screen.getByText("30 天内保持登录")).toBeInTheDocument();
    expect(screen.queryByText("INTERNAL")).not.toBeInTheDocument();
  });

  it("lets a Viewer without an Operator read the influencer library", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return response({
            department: {
              id: "department-1",
              name: "只读部",
              status: "active",
            },
            operator: null,
            role: "viewer",
            expires_at: "2026-08-12T00:00:00Z",
          });
        }
        if (url.includes("/influencers/filter-options")) {
          return response({ owners: [], tags: [], crm_stages: [] });
        }
        if (url.includes("/influencers")) {
          return response({ items: [], page: 1, page_size: 50, total: 0 });
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderAuthenticatedShell("influencers");

    await waitFor(() => {
      expect(
        screen.getAllByRole("heading", { name: "达人库", level: 2 }),
      ).toHaveLength(2);
    });
    expect(
      within(screen.getByRole("complementary")).getByText("待选择"),
    ).toBeInTheDocument();
    expect(screen.queryByText("选择当前操作人")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/operators"),
      ),
    ).toBe(false);
  });

  it("keeps Operator selection mandatory for the import workspace", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return response({
            department: {
              id: "department-1",
              name: "导入部",
              status: "active",
            },
            operator: null,
            role: "operator",
            expires_at: "2026-08-12T00:00:00Z",
          });
        }
        if (url.endsWith("/operators")) {
          return response([
            {
              id: "operator-1",
              department_id: "department-1",
              name: "导入操作人",
              role: "operator",
              status: "active",
            },
          ]);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderAuthenticatedShell("imports");

    expect(await screen.findByText("选择当前操作人")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "达人采集" }),
    ).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/operators"),
      ),
    ).toBe(true);
  });

  it("lets a Viewer without an Operator read imports while keeping mutations disabled", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return response({
            department: {
              id: "department-1",
              name: "只读部",
              status: "active",
            },
            operator: null,
            role: "viewer",
            expires_at: "2026-08-12T00:00:00Z",
          });
        }
        if (url.endsWith("/collection-jobs")) {
          return response([]);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderAuthenticatedShell("imports");

    expect(
      await screen.findByText("只读角色不能上传或确认导入。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建采集任务/ })).toBeDisabled();
    expect(screen.queryByText("选择当前操作人")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/operators"),
      ),
    ).toBe(false);
  });

  it("keeps Operator selection mandatory for Refresh Queue mutations", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return response({
            department: {
              id: "department-1",
              name: "数据部",
              status: "active",
            },
            operator: null,
            role: "operator",
            expires_at: "2026-08-12T00:00:00Z",
          });
        }
        if (url.endsWith("/operators")) {
          return response([
            {
              id: "operator-1",
              department_id: "department-1",
              name: "更新操作人",
              role: "operator",
              status: "active",
            },
          ]);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderAuthenticatedShell("refresh-queues");

    expect(await screen.findByText("选择当前操作人")).toBeInTheDocument();
    expect(screen.queryByText("创建更新名单")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/operators"),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/refresh-queues?"),
      ),
    ).toBe(false);
  });

  it("lets a Viewer without an Operator read Refresh Queues without write entry points", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return response({
            department: {
              id: "department-1",
              name: "只读部",
              status: "active",
            },
            operator: null,
            role: "viewer",
            expires_at: "2026-08-12T00:00:00Z",
          });
        }
        if (url.includes("/refresh-queues?")) {
          return response({ items: [], total: 0, offset: 0, limit: 50 });
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderAuthenticatedShell("refresh-queues");

    expect(await screen.findByText("暂无数据更新名单。")).toBeInTheDocument();
    expect(screen.queryByText("选择当前操作人")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /创建更新名单/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /数据更新/ })).toHaveAttribute(
      "href",
      "/refresh-queues",
    );
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/operators"),
      ),
    ).toBe(false);
  });

  it("keeps an existing Viewer Operator on the influencer workspace", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return response({
          department: { id: "department-1", name: "只读部", status: "active" },
          operator: {
            id: "operator-1",
            department_id: "department-1",
            name: "查看人",
            role: "viewer",
            status: "active",
          },
          role: "viewer",
          expires_at: "2026-08-12T00:00:00Z",
        });
      }
      if (url.includes("/influencers/filter-options")) {
        return response({ owners: [], tags: [], crm_stages: [] });
      }
      if (url.includes("/influencers")) {
        return response({ items: [], page: 1, page_size: 50, total: 0 });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    renderAuthenticatedShell("influencers");

    await waitFor(() => {
      expect(
        screen.getAllByRole("heading", { name: "达人库", level: 2 }),
      ).toHaveLength(2);
    });
    expect(screen.getByRole("link", { name: /数据采集/ })).toHaveAttribute(
      "href",
      "/",
    );
    expect(screen.getByRole("link", { name: /达人库/ })).toHaveAttribute(
      "href",
      "/influencers",
    );
    expect(screen.getByRole("link", { name: /达人库/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      within(screen.getByRole("complementary")).getByText("只读成员"),
    ).toBeInTheDocument();
    const userMenu = screen.getByRole("button", { name: "打开用户菜单" });
    expect(userMenu).toHaveTextContent("查看人");
    expect(userMenu).not.toHaveTextContent("只读成员");
    expect(userMenu).not.toHaveTextContent("只读部");
    expect(screen.queryByText("Campaign")).not.toBeInTheDocument();
    expect(screen.queryByText("Inbox")).not.toBeInTheDocument();
  });

  it("lets a Viewer without an Operator open influencer detail", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return response({
            department: {
              id: "department-1",
              name: "只读部",
              status: "active",
            },
            operator: null,
            role: "viewer",
            expires_at: "2026-08-12T00:00:00Z",
          });
        }
        if (url.endsWith("/influencers/influencer-1")) {
          return response({
            id: "influencer-1",
            display_name: "详情达人",
            status: "active",
            crm_stage: "待开发",
            owner: null,
            created_at: "2026-08-11T00:00:00Z",
            updated_at: "2026-08-11T00:00:00Z",
            platform_accounts: [],
            contacts: [],
            source_states: [],
            source_identities: [],
            current_metrics: [],
          });
        }
        if (url.includes("/metric-snapshots")) {
          return response({ items: [], page: 1, page_size: 50, total: 0 });
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderAuthenticatedShell("influencers", "influencer-1");

    expect(
      await screen.findByRole("heading", { name: "详情达人", level: 2 }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("complementary")).getByText("待选择"),
    ).toBeInTheDocument();
    expect(screen.queryByText("选择当前操作人")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/operators"),
      ),
    ).toBe(false);
  });

  it("logs out from the App Shell and returns to department login", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return response({
            department: {
              id: "department-1",
              name: "商务部",
              status: "active",
            },
            operator: {
              id: "operator-1",
              department_id: "department-1",
              name: "商务操作人",
              role: "operator",
              status: "active",
            },
            role: "operator",
            expires_at: "2026-08-12T00:00:00Z",
          });
        }
        if (url.endsWith("/auth/logout") && init?.method === "POST") {
          return response(null);
        }
        if (url.endsWith("/departments")) {
          return response([
            { id: "department-1", name: "商务部", status: "active" },
          ]);
        }
        if (url.endsWith("/collection-jobs")) return response([]);
        throw new Error(`Unexpected request: ${url}`);
      });

    renderAuthenticatedShell("imports");
    expect(await screen.findByText("达人采集")).toBeInTheDocument();
    fireEvent.click(
      within(screen.getByRole("complementary")).getByRole("button", {
        name: /退出登录/,
      }),
    );
    await waitFor(() =>
      expect(screen.getByText("请使用部门密码登录。")).toBeInTheDocument(),
    );
    expect(
      fetchMock.mock.calls.some(
        ([input, requestInit]) =>
          String(input).endsWith("/auth/logout") &&
          requestInit?.method === "POST",
      ),
    ).toBe(true);
  });
});
