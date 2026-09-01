import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, vi } from "vitest";

import { PermissionsWorkspace } from "../src/features/permissions/permissions-workspace";

type Operator = {
  id: string;
  name: string;
  role: "super_admin" | "manager" | "operator" | "viewer";
  status: "active" | "disabled";
  module_grants: string[];
  created_at: string;
  updated_at: string;
};

const operator: Operator = {
  id: "operator-1",
  name: "禁用操作人",
  role: "viewer",
  status: "disabled",
  module_grants: ["campaigns"],
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T01:00:00Z",
};

function response(data: unknown, status = 200) {
  return new Response(
    JSON.stringify({
      success: status < 400,
      data: status < 400 ? data : null,
      error:
        status < 400
          ? null
          : { code: data, message: `backend:${String(data)}`, details: null },
      request_id: "test",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

afterEach(() => vi.restoreAllMocks());

describe("PermissionsWorkspace", () => {
  it("loads disabled same-department operators from the admin API", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response([operator]));

    render(<PermissionsWorkspace onAuthRefresh={vi.fn()} />);

    expect(await screen.findByText("禁用操作人")).toBeInTheDocument();
    expect(screen.getByText("已禁用")).toBeInTheDocument();
    expect(screen.getByText("拓客活动")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/admin/operators",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("creates with the complete selected grant set and refetches server data", async () => {
    const refreshAuth = vi.fn().mockResolvedValue(undefined);
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/admin/operators") && !init?.method)
          return response([]);
        if (url.endsWith("/admin/operators") && init?.method === "POST")
          return response(operator);
        if (url.endsWith("/admin/operators")) return response([operator]);
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<PermissionsWorkspace onAuthRefresh={refreshAuth} />);
    await screen.findByText("暂无操作人");
    fireEvent.click(screen.getByRole("button", { name: /新建操作人/ }));
    fireEvent.change(screen.getByLabelText("姓名"), {
      target: { value: "新操作人" },
    });
    fireEvent.change(screen.getByLabelText("个人密码"), {
      target: { value: "operator-credential-123" },
    });
    fireEvent.click(screen.getByLabelText("今日触达"));
    fireEvent.click(screen.getByLabelText("候选池"));
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          if (
            !String(input).endsWith("/admin/operators") ||
            init?.method !== "POST"
          )
            return false;
          return (
            JSON.stringify({
              name: "新操作人",
              password: "operator-credential-123",
              role: "operator",
              module_grants: ["today_outreach", "candidate_pools"],
            }) === init.body
          );
        }),
      ).toBe(true),
    );
    expect(refreshAuth).toHaveBeenCalledTimes(1);
  });

  it("sends full state with expected_updated_at and refetches after VERSION_CONFLICT", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/admin/operators") && !init?.method)
          return response([operator]);
        if (url.endsWith(`/admin/operators/${operator.id}`) && !init?.method)
          return response(operator);
        if (
          url.endsWith(`/admin/operators/${operator.id}`) &&
          init?.method === "PATCH"
        ) {
          return response("VERSION_CONFLICT", 409);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<PermissionsWorkspace onAuthRefresh={vi.fn()} />);
    await screen.findByText("禁用操作人");
    fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
    await screen.findByDisplayValue("禁用操作人");
    expect(screen.getByText("Viewer 对已授权模块仅可查看")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          if (
            !String(input).endsWith(`/admin/operators/${operator.id}`) ||
            init?.method !== "PATCH"
          )
            return false;
          return (
            JSON.stringify({
              expected_updated_at: operator.updated_at,
              name: operator.name,
              role: operator.role,
              status: operator.status,
              module_grants: ["campaigns"],
            }) === init.body
          );
        }),
      ).toBe(true),
    );
    expect(
      await screen.findByText("数据已被其他操作更新，已重新加载最新数据。"),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/admin/operators"),
      ),
    ).toHaveLength(2);
  });

  it("renders Super Admin matrix as implicit, fixed access and Viewer wording as read-only", async () => {
    const superAdmin = {
      ...operator,
      id: "operator-sa",
      role: "super_admin" as const,
      module_grants: [],
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/admin/operators") && !init?.method)
        return response([superAdmin]);
      if (url.endsWith(`/admin/operators/${superAdmin.id}`))
        return response(superAdmin);
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<PermissionsWorkspace onAuthRefresh={vi.fn()} />);
    await screen.findByText("全部模块（隐式）");
    fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
    expect(
      await screen.findByText(
        "超级管理员拥有全部模块权限；不会保存单独模块授权。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "权限管理" })).toBeDisabled();
  });
});
