import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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

function renderAuthenticatedShell(workspace: "imports" | "influencers") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthShell workspace={workspace} />
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

    expect(
      await screen.findByRole("heading", { name: "达人库" }),
    ).toBeInTheDocument();
    expect(screen.getByText("待选择")).toBeInTheDocument();
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

    expect(
      await screen.findByRole("heading", { name: "达人库" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "导入工作区" })).toHaveAttribute(
      "href",
      "/",
    );
    expect(screen.getByRole("link", { name: "达人库" })).toHaveAttribute(
      "href",
      "/influencers",
    );
    expect(screen.getByText("Viewer")).toBeInTheDocument();
    expect(screen.queryByText("Campaign")).not.toBeInTheDocument();
    expect(screen.queryByText("Inbox")).not.toBeInTheDocument();
  });
});
