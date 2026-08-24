import { useLayoutEffect, useReducer, useRef, useState } from "react";

import type { CandidateCampaignSelection, CandidateMember } from "./types";

/**
 * Selection is deliberately scoped to an immutable Candidate Pool run.  It is
 * independent from any mutation target so it can also back future assignment
 * flows.
 */
export type CandidateSelectionScope = {
  candidatePoolId: string;
  runId: string;
};

export type CandidateSelectionIdentity = {
  scope: CandidateSelectionScope;
  generation: number;
};

export type CandidateSelectionState =
  | {
      scope: CandidateSelectionScope;
      mode: "EXPLICIT";
      /** Metadata is retained for UNKNOWN messaging and ambiguity handling. */
      selectedById: Record<string, CandidateMember>;
    }
  | {
      scope: CandidateSelectionScope;
      mode: "ALL_MATCH";
      matchCount: number;
      /** Only intentional removals are retained; MATCH IDs are never enumerated. */
      excludedById: Record<string, true>;
    };

export type CandidateSelectionAction =
  | { type: "RESET_SCOPE"; scope: CandidateSelectionScope }
  | { type: "TOGGLE_MEMBER"; member: CandidateMember; checked: boolean }
  | { type: "SELECT_PAGE"; members: CandidateMember[] }
  | { type: "DESELECT_PAGE"; members: CandidateMember[] }
  | { type: "CLEAR_ALL"; scope?: CandidateSelectionScope }
  | { type: "SELECT_ALL_MATCH"; matchCount: number };

export type CandidateSelectionPageState = {
  eligibleCount: number;
  selectedCount: number;
  checked: boolean;
  indeterminate: boolean;
  /** Members intentionally omitted from a page-select to avoid auto-picking an account. */
  blockedMemberIds: string[];
  blockedCount: number;
};

/** Keep both request modes within the API's public 10,000-ID contract. */
export const CANDIDATE_SELECTION_MAX_MEMBER_IDS = 10_000;
export const CANDIDATE_ALL_MATCH_MAX_EXCLUSIONS =
  CANDIDATE_SELECTION_MAX_MEMBER_IDS;

export const candidateSelectionActions = {
  resetScope: (scope: CandidateSelectionScope): CandidateSelectionAction => ({
    type: "RESET_SCOPE",
    scope,
  }),
  toggleMember: (
    member: CandidateMember,
    checked: boolean,
  ): CandidateSelectionAction => ({ type: "TOGGLE_MEMBER", member, checked }),
  selectPage: (members: CandidateMember[]): CandidateSelectionAction => ({
    type: "SELECT_PAGE",
    members,
  }),
  deselectPage: (members: CandidateMember[]): CandidateSelectionAction => ({
    type: "DESELECT_PAGE",
    members,
  }),
  /**
   * A scope makes delayed mutation callbacks harmless after a route/run change.
   * Omit it only for a synchronous, current-scope UI action.
   */
  clearAll: (scope?: CandidateSelectionScope): CandidateSelectionAction => ({
    type: "CLEAR_ALL",
    scope,
  }),
  selectAllMatch: (matchCount: number): CandidateSelectionAction => ({
    type: "SELECT_ALL_MATCH",
    matchCount,
  }),
};

function emptySelection(
  scope: CandidateSelectionScope,
): CandidateSelectionState {
  return {
    scope: { ...scope },
    mode: "EXPLICIT",
    selectedById: {},
  };
}

export function createCandidateSelection(
  scope: CandidateSelectionScope,
): CandidateSelectionState {
  return emptySelection(scope);
}

export function candidateSelectionScopesEqual(
  left: CandidateSelectionScope,
  right: CandidateSelectionScope,
): boolean {
  return (
    left.candidatePoolId === right.candidatePoolId && left.runId === right.runId
  );
}

export function candidateSelectionIdentitiesEqual(
  left: CandidateSelectionIdentity,
  right: CandidateSelectionIdentity,
): boolean {
  return (
    left.generation === right.generation &&
    candidateSelectionScopesEqual(left.scope, right.scope)
  );
}

function initialCandidateSelection({
  scope,
  selected,
}: {
  scope: CandidateSelectionScope;
  selected: CandidateMember[];
}): CandidateSelectionState {
  return selected.reduce(
    (selection, member) =>
      reduceCandidateSelection(
        selection,
        candidateSelectionActions.toggleMember(member, true),
      ),
    createCandidateSelection(scope),
  );
}

/**
 * Owns the rendered selection boundary for a Candidate Pool run. Keeping the
 * identity generation alongside the reducer makes delayed completion callbacks
 * harmless across A -> B -> A route transitions.
 */
export function useCandidateSelectionLifecycle({
  scope,
  selected = [],
}: {
  scope: CandidateSelectionScope;
  selected?: CandidateMember[];
}) {
  const selectionIdentityRef = useRef<CandidateSelectionIdentity>({
    scope,
    generation: 0,
  });
  const [selectionGeneration, setSelectionGeneration] = useState(0);
  useLayoutEffect(() => {
    if (
      !candidateSelectionScopesEqual(selectionIdentityRef.current.scope, scope)
    ) {
      const nextGeneration = selectionIdentityRef.current.generation + 1;
      selectionIdentityRef.current = {
        scope,
        generation: nextGeneration,
      };
      setSelectionGeneration(nextGeneration);
    }
  }, [scope.candidatePoolId, scope.runId]);
  const [storedSelection, dispatchSelection] = useReducer(
    reduceCandidateSelection,
    { scope, selected },
    initialCandidateSelection,
  );
  useLayoutEffect(() => {
    dispatchSelection(candidateSelectionActions.resetScope(scope));
  }, [scope.candidatePoolId, scope.runId]);
  const selection = candidateSelectionScopesEqual(storedSelection.scope, scope)
    ? storedSelection
    : createCandidateSelection(scope);

  return {
    dispatchSelection,
    selection,
    selectionGeneration,
    selectionIdentityRef,
  };
}

function isExplicitlyEligible(member: CandidateMember): boolean {
  return member.result === "MATCH" || member.result === "UNKNOWN";
}

function isScopedMember(
  selection: CandidateSelectionState,
  member: CandidateMember,
): boolean {
  return member.run_id === selection.scope.runId;
}

/** Whether the member can be changed in the current selection mode. */
export function isCandidateMemberSelectable(
  selection: CandidateSelectionState,
  member: CandidateMember,
): boolean {
  if (!isScopedMember(selection, member)) return false;
  if (selection.mode === "ALL_MATCH") return member.result === "MATCH";
  if (!isExplicitlyEligible(member)) return false;
  if (selection.selectedById[member.id]) return true;
  return !Object.values(selection.selectedById).some(
    (selected) => selected.influencer_id === member.influencer_id,
  );
}

export function isCandidateMemberSelected(
  selection: CandidateSelectionState,
  member: CandidateMember,
): boolean {
  if (!isScopedMember(selection, member)) return false;
  if (selection.mode === "EXPLICIT") {
    return Boolean(selection.selectedById[member.id]);
  }
  return member.result === "MATCH" && !selection.excludedById[member.id];
}

function normalizeMatchCount(matchCount: number): number {
  if (!Number.isFinite(matchCount)) return 0;
  return Math.max(0, Math.floor(matchCount));
}

export function candidateSelectionCount(
  selection: CandidateSelectionState,
): number {
  if (selection.mode === "EXPLICIT") {
    return Object.keys(selection.selectedById).length;
  }
  return Math.max(
    0,
    normalizeMatchCount(selection.matchCount) -
      Object.keys(selection.excludedById).length,
  );
}

/** Explicit metadata only; ALL_MATCH intentionally never materializes members. */
export function candidateSelectionMembers(
  selection: CandidateSelectionState,
): CandidateMember[] {
  return selection.mode === "EXPLICIT"
    ? Object.values(selection.selectedById)
    : [];
}

export function candidateSelectionUnknownCount(
  selection: CandidateSelectionState,
): number {
  return candidateSelectionMembers(selection).filter(
    (member) => member.result === "UNKNOWN",
  ).length;
}

function explicitPageSelectionMembers(
  selection: Extract<CandidateSelectionState, { mode: "EXPLICIT" }>,
  members: CandidateMember[],
): { eligible: CandidateMember[]; blockedMemberIds: string[] } {
  const explicitlyEligibleMembers = members.filter(
    (member) =>
      isScopedMember(selection, member) && isExplicitlyEligible(member),
  );
  const candidateMembers = explicitlyEligibleMembers.filter((member) =>
    isCandidateMemberSelectable(selection, member),
  );
  const groupedUnselected = new Map<string, CandidateMember[]>();
  for (const member of candidateMembers) {
    if (selection.selectedById[member.id]) continue;
    const group = groupedUnselected.get(member.influencer_id) ?? [];
    group.push(member);
    groupedUnselected.set(member.influencer_id, group);
  }
  const blocked = new Set(
    explicitlyEligibleMembers
      .filter((member) => !isCandidateMemberSelectable(selection, member))
      .map((member) => member.id),
  );
  for (const group of groupedUnselected.values()) {
    if (group.length > 1) {
      for (const member of group) blocked.add(member.id);
    }
  }
  return {
    eligible: candidateMembers.filter((member) => !blocked.has(member.id)),
    blockedMemberIds: [...blocked],
  };
}

function pageSelectionMembers(
  selection: CandidateSelectionState,
  members: CandidateMember[],
): { eligible: CandidateMember[]; blockedMemberIds: string[] } {
  if (selection.mode === "EXPLICIT") {
    return explicitPageSelectionMembers(selection, members);
  }
  return {
    eligible: members.filter((member) =>
      isCandidateMemberSelectable(selection, member),
    ),
    blockedMemberIds: [],
  };
}

export function candidateSelectionPageState(
  selection: CandidateSelectionState,
  members: CandidateMember[],
): CandidateSelectionPageState {
  const { eligible, blockedMemberIds } = pageSelectionMembers(
    selection,
    members,
  );
  const selectedCount = eligible.filter((member) =>
    isCandidateMemberSelected(selection, member),
  ).length;
  const checked = eligible.length > 0 && selectedCount === eligible.length;
  return {
    eligibleCount: eligible.length,
    selectedCount,
    checked,
    indeterminate: selectedCount > 0 && !checked,
    blockedMemberIds,
    blockedCount: blockedMemberIds.length,
  };
}

function explicitWithPage(
  selection: Extract<CandidateSelectionState, { mode: "EXPLICIT" }>,
  members: CandidateMember[],
  checked: boolean,
): CandidateSelectionState {
  if (
    checked &&
    candidateSelectionWouldExceedSelectionLimit(selection, members, true)
  ) {
    return selection;
  }
  const selectedById = { ...selection.selectedById };
  const pageMembers = checked
    ? explicitPageSelectionMembers(selection, members).eligible
    : members.filter((member) =>
        isCandidateMemberSelectable(selection, member),
      );
  for (const member of pageMembers) {
    if (checked) selectedById[member.id] = member;
    else delete selectedById[member.id];
  }
  return { ...selection, selectedById };
}

function allMatchWithPage(
  selection: Extract<CandidateSelectionState, { mode: "ALL_MATCH" }>,
  members: CandidateMember[],
  checked: boolean,
): CandidateSelectionState {
  const excludedById = { ...selection.excludedById };
  const eligibleMembers = members.filter((member) =>
    isCandidateMemberSelectable(selection, member),
  );
  if (
    !checked &&
    candidateSelectionWouldExceedExclusionLimit(
      selection,
      eligibleMembers,
      false,
    )
  ) {
    return selection;
  }
  for (const member of eligibleMembers) {
    if (checked) delete excludedById[member.id];
    else excludedById[member.id] = true;
  }
  return { ...selection, excludedById };
}

/**
 * Lets a caller present a clear message before an action would exceed the
 * bounded API request contract. In EXPLICIT mode this caps selected IDs; in
 * ALL_MATCH mode it caps exclusions only.
 */
export function candidateSelectionWouldExceedSelectionLimit(
  selection: CandidateSelectionState,
  members: CandidateMember[],
  checked: boolean,
): boolean {
  if (selection.mode === "EXPLICIT") {
    if (!checked) return false;
    const selectedIds = new Set(Object.keys(selection.selectedById));
    for (const member of explicitPageSelectionMembers(selection, members)
      .eligible) {
      selectedIds.add(member.id);
      if (selectedIds.size > CANDIDATE_SELECTION_MAX_MEMBER_IDS) return true;
    }
    return false;
  }
  if (checked) return false;
  const exclusionIds = new Set(Object.keys(selection.excludedById));
  for (const member of members) {
    if (!isCandidateMemberSelectable(selection, member)) continue;
    exclusionIds.add(member.id);
    if (exclusionIds.size > CANDIDATE_ALL_MATCH_MAX_EXCLUSIONS) return true;
  }
  return false;
}

/** @deprecated Use candidateSelectionWouldExceedSelectionLimit instead. */
export function candidateSelectionWouldExceedExclusionLimit(
  selection: CandidateSelectionState,
  members: CandidateMember[],
  checked: boolean,
): boolean {
  return candidateSelectionWouldExceedSelectionLimit(
    selection,
    members,
    checked,
  );
}

export function reduceCandidateSelection(
  selection: CandidateSelectionState,
  action: CandidateSelectionAction,
): CandidateSelectionState {
  switch (action.type) {
    case "RESET_SCOPE":
      return candidateSelectionScopesEqual(selection.scope, action.scope)
        ? selection
        : emptySelection(action.scope);
    case "CLEAR_ALL":
      if (
        action.scope &&
        !candidateSelectionScopesEqual(selection.scope, action.scope)
      ) {
        return selection;
      }
      return emptySelection(selection.scope);
    case "SELECT_ALL_MATCH":
      return {
        scope: { ...selection.scope },
        mode: "ALL_MATCH",
        matchCount: normalizeMatchCount(action.matchCount),
        excludedById: {},
      };
    case "TOGGLE_MEMBER":
      if (!isCandidateMemberSelectable(selection, action.member)) {
        return selection;
      }
      return selection.mode === "EXPLICIT"
        ? explicitWithPage(selection, [action.member], action.checked)
        : allMatchWithPage(selection, [action.member], action.checked);
    case "SELECT_PAGE":
      return selection.mode === "EXPLICIT"
        ? explicitWithPage(selection, action.members, true)
        : allMatchWithPage(selection, action.members, true);
    case "DESELECT_PAGE":
      return selection.mode === "EXPLICIT"
        ? explicitWithPage(selection, action.members, false)
        : allMatchWithPage(selection, action.members, false);
  }
}

/** Returns null for an empty selection, which keeps mutation controls disabled. */
export function candidateSelectionPayload(
  selection: CandidateSelectionState,
): CandidateCampaignSelection | null {
  if (candidateSelectionCount(selection) === 0) return null;
  if (selection.mode === "EXPLICIT") {
    return {
      run_id: selection.scope.runId,
      member_ids: Object.keys(selection.selectedById),
    };
  }
  return {
    run_id: selection.scope.runId,
    selection_mode: "ALL_MATCH",
    excluded_member_ids: Object.keys(selection.excludedById),
  };
}
