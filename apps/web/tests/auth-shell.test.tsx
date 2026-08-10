import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { AuthShell } from "../src/components/auth-shell";

afterEach(() => {
  vi.restoreAllMocks();
});

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
});
