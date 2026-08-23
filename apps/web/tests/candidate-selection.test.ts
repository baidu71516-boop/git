import { describe, expect, it } from "vitest";

import {
  CANDIDATE_SELECTION_MAX_MEMBER_IDS,
  candidateSelectionActions,
  candidateSelectionCount,
  candidateSelectionMembers,
  candidateSelectionPageState,
  candidateSelectionPayload,
  candidateSelectionUnknownCount,
  candidateSelectionWouldExceedSelectionLimit,
  createCandidateSelection,
  isCandidateMemberSelected,
  isCandidateMemberSelectable,
  reduceCandidateSelection,
  type CandidateSelectionState,
} from "../src/features/candidate-pools/candidate-selection";
import type { CandidateMember } from "../src/features/candidate-pools/types";

const scope = { candidatePoolId: "pool-1", runId: "run-1" };

function member(
  id: string,
  result: CandidateMember["result"] = "MATCH",
  options: Partial<Pick<CandidateMember, "influencer_id" | "run_id">> = {},
): CandidateMember {
  return {
    id,
    run_id: options.run_id ?? scope.runId,
    influencer_id: options.influencer_id ?? `influencer-${id}`,
    platform_account_id: `account-${id}`,
    result,
    reason_codes: [],
    redacted_evidence: {},
    evidence_hash: `hash-${id}`,
    created_at: "2026-08-23T00:00:00Z",
    updated_at: "2026-08-23T00:00:00Z",
    influencer: {
      id: options.influencer_id ?? `influencer-${id}`,
      display_name: `达人 ${id}`,
      status: "active",
    },
    platform_account: {
      id: `account-${id}`,
      platform: "douyin",
      platform_account_id: id,
      account_name: `账号 ${id}`,
      account_handle: id,
      is_active: true,
    },
  };
}

function reduce(actions: Parameters<typeof reduceCandidateSelection>[1][]) {
  return actions.reduce(
    reduceCandidateSelection,
    createCandidateSelection(scope),
  );
}

describe("Candidate Pool selection domain", () => {
  it("models current-page checkbox states without selecting NOT_MATCH", () => {
    const match = member("match");
    const unknown = member("unknown", "UNKNOWN");
    const notMatch = member("not-match", "NOT_MATCH");
    const page = [match, unknown, notMatch];
    let selection = createCandidateSelection(scope);

    expect(candidateSelectionPageState(selection, page)).toMatchObject({
      eligibleCount: 2,
      selectedCount: 0,
      checked: false,
      indeterminate: false,
    });

    selection = reduceCandidateSelection(
      selection,
      candidateSelectionActions.toggleMember(match, true),
    );
    expect(candidateSelectionPageState(selection, page)).toMatchObject({
      eligibleCount: 2,
      selectedCount: 1,
      checked: false,
      indeterminate: true,
    });

    selection = reduceCandidateSelection(
      selection,
      candidateSelectionActions.selectPage(page),
    );
    expect(candidateSelectionPageState(selection, page)).toMatchObject({
      eligibleCount: 2,
      selectedCount: 2,
      checked: true,
      indeterminate: false,
    });
    expect(candidateSelectionCount(selection)).toBe(2);
    expect(candidateSelectionUnknownCount(selection)).toBe(1);
    expect(candidateSelectionPayload(selection)).toEqual({
      run_id: "run-1",
      member_ids: ["match", "unknown"],
    });

    selection = reduceCandidateSelection(
      selection,
      candidateSelectionActions.deselectPage(page),
    );
    expect(candidateSelectionCount(selection)).toBe(0);
    expect(candidateSelectionPayload(selection)).toBeNull();
  });

  it("preserves explicit cross-page selections across result tabs and page sizes", () => {
    const firstPage = [member("one"), member("two")];
    const secondPage = [member("three"), member("four", "UNKNOWN")];
    const selection = reduce([
      candidateSelectionActions.selectPage(firstPage),
      candidateSelectionActions.selectPage(secondPage),
      candidateSelectionActions.resetScope(scope),
    ]);

    expect(candidateSelectionCount(selection)).toBe(4);
    expect(candidateSelectionMembers(selection).map((item) => item.id)).toEqual(
      ["one", "two", "three", "four"],
    );
    expect(candidateSelectionPageState(selection, firstPage)).toMatchObject({
      checked: true,
      selectedCount: 2,
    });
    expect(candidateSelectionPageState(selection, secondPage)).toMatchObject({
      checked: true,
      selectedCount: 2,
    });
  });

  it("resets on a different Candidate Pool run and clear-all returns to empty explicit mode", () => {
    const selected = reduce([
      candidateSelectionActions.toggleMember(member("one"), true),
    ]);
    const changedRun = reduceCandidateSelection(
      selected,
      candidateSelectionActions.resetScope({
        candidatePoolId: "pool-1",
        runId: "run-2",
      }),
    );
    expect(changedRun).toMatchObject({
      mode: "EXPLICIT",
      scope: { candidatePoolId: "pool-1", runId: "run-2" },
      selectedById: {},
    });

    const cleared = reduceCandidateSelection(
      selected,
      candidateSelectionActions.clearAll(),
    );
    expect(cleared).toMatchObject({ mode: "EXPLICIT", selectedById: {} });
    expect(candidateSelectionCount(cleared)).toBe(0);
  });

  it("models ALL_MATCH as count minus exclusions without browser UUID enumeration", () => {
    const match = member("match");
    const unknown = member("unknown", "UNKNOWN");
    let selection = reduce([
      candidateSelectionActions.toggleMember(unknown, true),
      candidateSelectionActions.selectAllMatch(5),
    ]);

    expect(selection).toMatchObject({
      mode: "ALL_MATCH",
      matchCount: 5,
      excludedById: {},
    });
    expect("selectedById" in selection).toBe(false);
    expect(candidateSelectionCount(selection)).toBe(5);
    expect(candidateSelectionUnknownCount(selection)).toBe(0);
    expect(isCandidateMemberSelected(selection, match)).toBe(true);
    expect(isCandidateMemberSelected(selection, unknown)).toBe(false);

    selection = reduceCandidateSelection(
      selection,
      candidateSelectionActions.toggleMember(match, false),
    );
    expect(candidateSelectionCount(selection)).toBe(4);
    expect(isCandidateMemberSelected(selection, match)).toBe(false);
    expect(candidateSelectionPayload(selection)).toEqual({
      run_id: "run-1",
      selection_mode: "ALL_MATCH",
      excluded_member_ids: ["match"],
    });

    selection = reduceCandidateSelection(
      selection,
      candidateSelectionActions.selectPage([match, unknown]),
    );
    expect(candidateSelectionCount(selection)).toBe(5);
    expect(candidateSelectionPayload(selection)).toEqual({
      run_id: "run-1",
      selection_mode: "ALL_MATCH",
      excluded_member_ids: [],
    });
    expect(isCandidateMemberSelectable(selection, unknown)).toBe(false);
  });

  it("never auto-selects multiple platform accounts for one influencer in explicit mode", () => {
    const firstAccount = member("first", "MATCH", {
      influencer_id: "influencer-shared",
    });
    const secondAccount = member("second", "MATCH", {
      influencer_id: "influencer-shared",
    });
    const independent = member("independent");
    let selection = createCandidateSelection(scope);

    const initialPage = candidateSelectionPageState(selection, [
      firstAccount,
      secondAccount,
      independent,
    ]);
    expect(initialPage).toMatchObject({
      eligibleCount: 1,
      blockedCount: 2,
      blockedMemberIds: ["first", "second"],
    });

    selection = reduceCandidateSelection(
      selection,
      candidateSelectionActions.selectPage([
        firstAccount,
        secondAccount,
        independent,
      ]),
    );
    expect(candidateSelectionMembers(selection).map((item) => item.id)).toEqual(
      ["independent"],
    );

    selection = reduceCandidateSelection(
      selection,
      candidateSelectionActions.toggleMember(firstAccount, true),
    );
    expect(isCandidateMemberSelected(selection, firstAccount)).toBe(true);
    expect(isCandidateMemberSelectable(selection, secondAccount)).toBe(false);
    expect(
      candidateSelectionPageState(selection, [firstAccount, secondAccount]),
    ).toMatchObject({
      eligibleCount: 1,
      selectedCount: 1,
      blockedMemberIds: ["second"],
      blockedCount: 1,
    });

    selection = reduceCandidateSelection(
      selection,
      candidateSelectionActions.toggleMember(secondAccount, true),
    );
    expect(isCandidateMemberSelected(selection, secondAccount)).toBe(false);

    selection = reduceCandidateSelection(
      selection,
      candidateSelectionActions.toggleMember(firstAccount, false),
    );
    expect(isCandidateMemberSelected(selection, firstAccount)).toBe(false);
  });

  it("rejects a row from another run without disturbing the scoped selection", () => {
    const valid = member("valid");
    const stale = member("stale", "MATCH", { run_id: "run-older" });
    const selection = reduce([
      candidateSelectionActions.toggleMember(valid, true),
      candidateSelectionActions.toggleMember(stale, true),
    ]);

    expect(candidateSelectionMembers(selection).map((item) => item.id)).toEqual(
      ["valid"],
    );
    expect(isCandidateMemberSelected(selection, stale)).toBe(false);
  });

  it("keeps both selection modes inside the 10,000-ID request bound", () => {
    const retained = member("retained");
    const overflow = member("overflow");
    const explicitAtLimit: CandidateSelectionState = {
      scope,
      mode: "EXPLICIT",
      selectedById: Object.fromEntries(
        Array.from(
          { length: CANDIDATE_SELECTION_MAX_MEMBER_IDS },
          (_, index) => [`selected-${index}`, retained],
        ),
      ),
    };
    expect(
      candidateSelectionWouldExceedSelectionLimit(
        explicitAtLimit,
        [overflow],
        true,
      ),
    ).toBe(true);
    expect(
      reduceCandidateSelection(
        explicitAtLimit,
        candidateSelectionActions.toggleMember(overflow, true),
      ),
    ).toBe(explicitAtLimit);

    const allMatchAtLimit: CandidateSelectionState = {
      scope,
      mode: "ALL_MATCH",
      matchCount: CANDIDATE_SELECTION_MAX_MEMBER_IDS + 1,
      excludedById: Object.fromEntries(
        Array.from(
          { length: CANDIDATE_SELECTION_MAX_MEMBER_IDS },
          (_, index) => [`excluded-${index}`, true],
        ),
      ),
    };
    expect(
      candidateSelectionWouldExceedSelectionLimit(
        allMatchAtLimit,
        [overflow],
        false,
      ),
    ).toBe(true);
    expect(
      reduceCandidateSelection(
        allMatchAtLimit,
        candidateSelectionActions.toggleMember(overflow, false),
      ),
    ).toBe(allMatchAtLimit);
  });

  it("ignores delayed scoped clears after the user has changed runs", () => {
    const selected = reduce([
      candidateSelectionActions.toggleMember(member("one"), true),
    ]);
    const staleClear = reduceCandidateSelection(
      selected,
      candidateSelectionActions.clearAll({
        candidatePoolId: "pool-1",
        runId: "run-older",
      }),
    );
    expect(staleClear).toBe(selected);
    expect(candidateSelectionCount(staleClear)).toBe(1);
  });
});
