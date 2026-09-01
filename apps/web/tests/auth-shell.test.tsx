import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { Key, ReactNode } from "react";
import { afterEach, vi } from "vitest";

import { AuthShell } from "../src/components/auth-shell";

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

vi.mock("next/navigation", () => ({
  usePathname: () => "/influencers",
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const queryClients: QueryClient[] = [];
const renderedShells: ReturnType<typeof render>[] = [];

afterEach(async () => {
  try {
    for (const view of renderedShells.splice(0).reverse()) view.unmount();
    const clients = queryClients.splice(0);
    await Promise.all(clients.map((client) => client.cancelQueries()));
    for (const client of clients) {
      expect(client.isFetching()).toBe(0);
      expect(client.isMutating()).toBe(0);
      client.clear();
    }
  } finally {
    vi.restoreAllMocks();
  }
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
  workspace:
    | "imports"
    | "import-jobs"
    | "influencers"
    | "refresh-queues"
    | "campaigns"
    | "permissions",
  influencerId?: string,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClients.push(queryClient);
  const view = render(
    <QueryClientProvider client={queryClient}>
      <AuthShell workspace={workspace} influencerId={influencerId} />
    </QueryClientProvider>,
  );
  renderedShells.push(view);
  return view;
}

describe("AuthShell", () => {
  it("uses effective_role, not the Super Admin Department ceiling, for header and permissions navigation", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return response({
            department: {
              id: "department-1",
              name: "权限部",
              status: "active",
            },
            operator: {
              id: "operator-1",
              department_id: "department-1",
              name: "查看人",
              role: "viewer",
              status: "active",
            },
            role: "super_admin",
            department_role_ceiling: "super_admin",
            effective_role: "viewer",
            expires_at: "2026-08-20T00:00:00Z",
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderAuthenticatedShell("permissions");

    expect(
      await screen.findByText("当前身份无权访问权限管理"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "权限管理" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "打开用户菜单" }),
    ).toHaveTextContent("只读成员");
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/admin/operators"),
      ),
    ).toBe(false);
  });

  it("shows the permissions page and navigation only for an effective Super Admin", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return response({
          department: { id: "department-1", name: "权限部", status: "active" },
          operator: {
            id: "operator-1",
            department_id: "department-1",
            name: "管理员",
            role: "super_admin",
            status: "active",
          },
          role: "super_admin",
          department_role_ceiling: "super_admin",
          effective_role: "super_admin",
          expires_at: "2026-08-20T00:00:00Z",
        });
      }
      if (url.endsWith("/admin/operators")) return response([]);
      throw new Error(`Unexpected request: ${url}`);
    });

    renderAuthenticatedShell("permissions");

    expect(
      await screen.findByRole("button", { name: /新建操作人/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "权限管理" })).toHaveAttribute(
      "href",
      "/admin/permissions",
    );
  });

  it("shows 待选择 without a current Super Admin role when no Operator is selected", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return response({
          department: { id: "department-1", name: "权限部", status: "active" },
          operator: null,
          role: "super_admin",
          department_role_ceiling: "super_admin",
          effective_role: null,
          expires_at: "2026-08-20T00:00:00Z",
        });
      }
      if (url.endsWith("/operators")) return response([]);
      throw new Error(`Unexpected request: ${url}`);
    });

    renderAuthenticatedShell("permissions");

    expect((await screen.findAllByText("待选择")).length).toBeGreaterThan(0);
    expect(screen.queryByText("超级管理员")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "权限管理" }),
    ).not.toBeInTheDocument();
  });

  it("clears stale Super Admin UI after a successful self mutation revokes the session", async () => {
    const self = {
      id: "operator-self",
      name: "当前管理员",
      role: "super_admin",
      status: "active",
      module_grants: [],
      created_at: "2026-08-20T00:00:00Z",
      updated_at: "2026-08-20T01:00:00Z",
    };
    const backup = { ...self, id: "operator-backup", name: "备用管理员" };
    let authMeCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        authMeCalls += 1;
        if (authMeCalls > 1) {
          return new Response(
            JSON.stringify({
              success: false,
              data: null,
              error: {
                code: "INVALID_SESSION",
                message: "session revoked",
                details: null,
              },
              request_id: "test",
            }),
            { status: 401, headers: { "Content-Type": "application/json" } },
          );
        }
        return response({
          department: { id: "department-1", name: "权限部", status: "active" },
          operator: {
            id: self.id,
            department_id: "department-1",
            name: self.name,
            role: self.role,
            status: "active",
          },
          role: "super_admin",
          department_role_ceiling: "super_admin",
          effective_role: "super_admin",
          expires_at: "2026-08-20T00:00:00Z",
        });
      }
      if (url.endsWith("/admin/operators") && !init?.method)
        return response([self, backup]);
      if (url.endsWith(`/admin/operators/${self.id}`) && !init?.method)
        return response(self);
      if (
        url.endsWith(`/admin/operators/${self.id}`) &&
        init?.method === "PATCH"
      )
        return response({ ...self, status: "disabled" });
      if (url.endsWith("/departments")) return response([]);
      throw new Error(`Unexpected request: ${url}`);
    });

    renderAuthenticatedShell("permissions");
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: /编\s*辑/ })).toHaveLength(
        2,
      ),
    );
    fireEvent.click(screen.getAllByRole("button", { name: /编\s*辑/ })[0]);
    await screen.findByDisplayValue(self.name);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "状态" }));
    fireEvent.click(await screen.findByRole("option", { name: "禁用" }));
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    expect(await screen.findByText("请使用部门密码登录。")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "权限管理" }),
    ).not.toBeInTheDocument();
  });

  it("requires a selected Operator before opening a business workspace", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return response({
            department: {
              id: "department-1",
              name: "拓展部",
              status: "active",
            },
            operator: null,
            role: "operator",
            department_role_ceiling: "operator",
            effective_role: null,
            expires_at: "2026-08-20T00:00:00Z",
          });
        }
        if (url.endsWith("/operators")) {
          return response([
            {
              id: "operator-1",
              department_id: "department-1",
              name: "当前操作人",
              role: "operator",
              status: "active",
            },
          ]);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderAuthenticatedShell("campaigns");

    expect(await screen.findByText("选择当前操作人")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/operators"),
      ),
    ).toBe(true);
  });

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
    expect(
      screen.getByRole("link", { name: "鄂ICP备2026044999号" }),
    ).toBeInTheDocument();
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
    expect(
      screen.getByRole("link", { name: "鄂ICP备2026044999号" }),
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

  it("does not infer a Viewer identity from a Department ceiling", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
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
          department_role_ceiling: "viewer",
          effective_role: null,
          expires_at: "2026-08-12T00:00:00Z",
        });
      }
      if (url.endsWith("/operators")) {
        return response([]);
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    renderAuthenticatedShell("imports");

    expect(await screen.findByText("选择当前操作人")).toBeInTheDocument();
    expect(screen.queryByText("只读成员")).not.toBeInTheDocument();
  });

  it.each(["viewer", "operator", "manager", "super_admin"] as const)(
    "lets an authenticated %s without an Operator read Import Job history in Backend scope",
    async (role) => {
      const fetchMock = vi
        .spyOn(globalThis, "fetch")
        .mockImplementation(async (input, init) => {
          const url = String(input);
          if (url.endsWith("/auth/me")) {
            return response({
              department: {
                id: "department-1",
                name: "数据部",
                status: "active",
              },
              operator: null,
              role,
              expires_at: "2026-08-12T00:00:00Z",
            });
          }
          if (url.endsWith("/import-jobs?offset=0&limit=50")) {
            return response({ items: [], total: 0, offset: 0, limit: 50 });
          }
          throw new Error(
            `Unexpected request: ${url} ${init?.method ?? "GET"}`,
          );
        });

      renderAuthenticatedShell("import-jobs");

      expect(await screen.findByText("暂无导入记录")).toBeInTheDocument();
      expect(screen.queryByText("选择当前操作人")).not.toBeInTheDocument();
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: /导入记录/ })).toHaveAttribute(
        "href",
        "/import-jobs",
      );
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith("/operators"),
        ),
      ).toBe(false);
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith("/departments"),
        ),
      ).toBe(false);
      expect(
        fetchMock.mock.calls.every(
          ([input]) => String(input).includes("department_id=") === false,
        ),
      ).toBe(true);
      expect(fetchMock.mock.calls.every(([, init]) => !init?.method)).toBe(
        true,
      );
    },
  );

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

  it("does not infer a Viewer identity for Refresh Queues", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
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
          department_role_ceiling: "viewer",
          effective_role: null,
          expires_at: "2026-08-12T00:00:00Z",
        });
      }
      if (url.endsWith("/operators")) {
        return response([]);
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    renderAuthenticatedShell("refresh-queues");

    expect(await screen.findByText("选择当前操作人")).toBeInTheDocument();
    expect(screen.queryByText("只读成员")).not.toBeInTheDocument();
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
          department_role_ceiling: "super_admin",
          effective_role: "viewer",
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
    expect(userMenu).toHaveTextContent("只读成员");
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
            department_role_ceiling: "viewer",
            effective_role: null,
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
            department_role_ceiling: "super_admin",
            effective_role: "operator",
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
