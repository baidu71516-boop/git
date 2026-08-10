import { render, screen } from "@testing-library/react";

import { PhaseZeroStatus } from "../src/components/phase-zero-status";

describe("PhaseZeroStatus", () => {
  it("states that business integrations are disabled", () => {
    render(<PhaseZeroStatus />);
    expect(screen.getByText("达人智能触达系统")).toBeInTheDocument();
    expect(
      screen.getByText(/尚未启用真实 AI、邮件或业务操作/),
    ).toBeInTheDocument();
  });
});
