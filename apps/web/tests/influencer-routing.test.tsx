import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DrawerSlotCatchAll from "../src/app/influencers/@drawer/[...catchAll]/page";
import DrawerSlotDefault from "../src/app/influencers/@drawer/default";
import DrawerSlotPage from "../src/app/influencers/@drawer/page";
import InfluencersLayout from "../src/app/influencers/layout";

describe("influencer parallel route structure", () => {
  it("renders the list and drawer slots together", () => {
    render(
      <InfluencersLayout drawer={<aside>抽屉内容</aside>}>
        <main>达人库内容</main>
      </InfluencersLayout>,
    );

    expect(screen.getByText("达人库内容")).toBeInTheDocument();
    expect(screen.getByText("抽屉内容")).toBeInTheDocument();
  });

  it("uses explicit null fallbacks for inactive and unmatched drawer routes", () => {
    expect(DrawerSlotDefault()).toBeNull();
    expect(DrawerSlotPage()).toBeNull();
    expect(DrawerSlotCatchAll()).toBeNull();
  });
});
