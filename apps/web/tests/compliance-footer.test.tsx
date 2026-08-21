import { render, screen } from "@testing-library/react";

import { ComplianceFooter } from "../src/components/compliance-footer";

describe("ComplianceFooter", () => {
  it("renders the ICP record as a safe external link", () => {
    render(<ComplianceFooter />);

    const link = screen.getByRole("link", { name: "鄂ICP备2026044999号" });
    expect(link).toHaveAttribute("href", "https://beian.miit.gov.cn/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByText(/公安联网备案/)).not.toBeInTheDocument();
  });
});
