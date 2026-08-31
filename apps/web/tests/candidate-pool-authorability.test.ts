import { describe, expect, it } from "vitest";

import {
  isAuthorableSellerTargetingPolicy,
  type TargetingPolicyDefinition,
} from "../src/features/candidate-pools/types";

function sellerDefinition(
  fields: Record<string, unknown> = {},
): TargetingPolicyDefinition {
  return {
    schema_version: 1,
    policy_type: "SELLER_V1",
    ...fields,
  } as TargetingPolicyDefinition;
}

describe("isAuthorableSellerTargetingPolicy", () => {
  it.each([
    [
      "accepts an API round-trip nullable SELLER_V1",
      sellerDefinition({
        contact_availability: null,
        content_activity: null,
        long_inactivity: null,
      }),
      true,
    ],
    ["accepts absent nullable fields", sellerDefinition(), true],
    [
      "accepts a current string contact rule",
      sellerDefinition({ contact_availability: "has_email" }),
      true,
    ],
    [
      "rejects an old object contact rule",
      sellerDefinition({ contact_availability: { types: ["email"] } }),
      false,
    ],
    [
      "rejects non-null content activity",
      sellerDefinition({
        content_activity: { schema_version: 1, minimum_inactive_days: 60 },
      }),
      false,
    ],
    [
      "rejects non-null long inactivity",
      sellerDefinition({
        long_inactivity: { schema_version: 1, minimum_inactive_days: 90 },
      }),
      false,
    ],
  ])("%s", (_description, definition, expected) => {
    expect(isAuthorableSellerTargetingPolicy(definition)).toBe(expected);
  });
});
