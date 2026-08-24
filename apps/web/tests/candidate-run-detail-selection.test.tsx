import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CandidateRunDetailView } from "../src/features/candidate-pools/candidate-run-detail-view";
import {
  candidateSelectionActions,
  candidateSelectionCount,
  useCandidateSelectionLifecycle,
} from "../src/features/candidate-pools/candidate-selection";
import { candidatePoolQueryKeys } from "../src/features/candidate-pools/queries";
import type {
  CandidateMember,
  CandidateMemberPage,
  CandidateMemberPageSize,
  CandidatePoolRun,
} from "../src/features/candidate-pools/types";
import type { Campaign } from "../src/features/campaigns/types";

const poolId = "pool-selection";
const runId = "run-selection";

function member(id: string, run = runId): CandidateMember {
  return {
    id,
    run_id: run,
    influencer_id: `influencer-${id}`,
    platform_account_id: `account-${id}`,
    result: "MATCH",
    reason_codes: [],
    redacted_evidence: {},
    evidence_hash: `hash-${id}`,
    created_at: "2026-08-23T00:00:00Z",
    updated_at: "2026-08-23T00:00:00Z",
    influencer: {
      id: `influencer-${id}`,
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

function memberCheckboxName(id: string) {
  return `选择 达人 ${id}：抖音 · 账号 ${id} @${id}（候选记录 ${id}）`;
}

function rowCheckbox(id: string) {
  return screen.getByRole("checkbox", { name: memberCheckboxName(id) });
}

function completedRun(id = runId, matchCount = 4): CandidatePoolRun {
  return {
    id,
    pool_id: poolId,
    policy_id: "policy-selection",
    as_of: "2026-08-23T00:00:00Z",
    input_watermark: null,
    status: "COMPLETED",
    match_count: matchCount,
    unknown_count: 0,
    not_match_count: 0,
    error_code: null,
    error_message: null,
    created_at: "2026-08-23T00:00:00Z",
    updated_at: "2026-08-23T00:00:00Z",
    idempotent_replay: false,
  };
}

function page(
  items: CandidateMember[],
  next_cursor: string | null = null,
): CandidateMemberPage {
  return { items, next_cursor };
}

function seedRun(
  client: QueryClient,
  {
    id = runId,
    pages,
    pageSize = 50,
    matchCount = 4,
    result,
  }: {
    id?: string;
    pages: CandidateMemberPage[];
    pageSize?: CandidateMemberPageSize;
    matchCount?: number;
    result?: "MATCH" | "UNKNOWN";
  },
) {
  client.setQueryData(
    candidatePoolQueryKeys.run(poolId, id),
    completedRun(id, matchCount),
  );
  client.setQueryData(candidatePoolQueryKeys.policies(poolId), []);
  client.setQueryData(
    candidatePoolQueryKeys.members(poolId, id, result, pageSize),
    {
      pages,
      pageParams: pages.map((_, index) =>
        index === 0 ? null : `cursor-${index}`,
      ),
    },
  );
}

function campaign(): Campaign {
  return {
    id: "campaign-selection",
    department_id: "department-selection",
    owner_operator_id: "owner-selection",
    owner: { id: "owner-selection", name: "负责人", status: "active" },
    created_by_operator_id: "owner-selection",
    name: "目标拓客活动",
    status: "DRAFT",
    review_mode: "MANUAL",
    review_count: null,
    duplicate_history_policy: "ALLOW",
    duplicate_window_days: null,
    version: 1,
    created_at: "2026-08-23T00:00:00Z",
    updated_at: "2026-08-23T00:00:00Z",
  };
}

function seedCampaignSelector(client: QueryClient) {
  client.setQueryData(["candidate-pools", "campaign-selector"], {
    pages: [{ items: [campaign()], next_cursor: null }],
    pageParams: [null],
  });
}

function apiResponse(data: unknown, status = 200, error: unknown = null) {
  return new Response(
    JSON.stringify({
      success: status >= 200 && status < 300,
      data: status >= 200 && status < 300 ? data : null,
      error,
      request_id: "candidate-selection-test",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

const queryClients: QueryClient[] = [];
const renderedViews: ReturnType<typeof render>[] = [];

afterEach(() => {
  for (const view of renderedViews.splice(0).reverse()) view.unmount();
  for (const queryClient of queryClients.splice(0)) {
    expect(queryClient.isFetching()).toBe(0);
    expect(queryClient.isMutating()).toBe(0);
    queryClient.clear();
  }
  vi.restoreAllMocks();
});

function candidateRunView(client: QueryClient, id = runId) {
  return (
    <QueryClientProvider client={client}>
      <CandidateRunDetailView
        hasSelectedOperator
        poolId={poolId}
        role="manager"
        runId={id}
      />
    </QueryClientProvider>
  );
}

function renderView(client: QueryClient, id = runId) {
  const view = render(candidateRunView(client, id));
  renderedViews.push(view);
  return view;
}

function rerenderView(
  view: ReturnType<typeof render>,
  queryClient: QueryClient,
  id: string,
) {
  view.rerender(candidateRunView(queryClient, id));
}

function CandidateSelectionLifecycleHarness({
  candidate,
  currentRunId,
}: {
  candidate: CandidateMember;
  currentRunId: string;
}) {
  const { dispatchSelection, selection } = useCandidateSelectionLifecycle({
    scope: { candidatePoolId: poolId, runId: currentRunId },
  });
  return (
    <section aria-label="候选选择生命周期">
      <output aria-label="选择作用域">{selection.scope.runId}</output>
      <output aria-label="选择数量">
        {candidateSelectionCount(selection)}
      </output>
      <button
        onClick={() =>
          dispatchSelection(
            candidateSelectionActions.toggleMember(candidate, true),
          )
        }
        type="button"
      >
        选择 {candidate.id}
      </button>
    </section>
  );
}

function renderSelectionLifecycle(
  candidate: CandidateMember,
  currentRunId = runId,
) {
  const view = render(
    <CandidateSelectionLifecycleHarness
      candidate={candidate}
      currentRunId={currentRunId}
    />,
  );
  renderedViews.push(view);
  return view;
}

function trackMessageChannels() {
  const NativeMessageChannel = globalThis.MessageChannel;
  const channels: MessageChannel[] = [];

  class TrackedMessageChannel extends NativeMessageChannel {
    constructor() {
      super();
      let onmessage: ((event: MessageEvent) => void) | null = null;
      Object.defineProperty(this.port1, "onmessage", {
        configurable: true,
        get: () => onmessage,
        set: (listener: ((event: MessageEvent) => void) | null) => {
          onmessage = listener;
        },
      });
      this.port1.addEventListener("message", (event) => {
        if (onmessage) {
          act(() => onmessage?.(event));
        }
      });
      this.port1.start();
      channels.push(this);
    }
  }

  vi.stubGlobal("MessageChannel", TrackedMessageChannel);
  return () => {
    for (const channel of channels) {
      channel.port1.onmessage = null;
      channel.port1.close();
      channel.port2.close();
    }
    vi.unstubAllGlobals();
  };
}

async function choosePageSize(pageSize: HTMLElement, value: string) {
  const releaseMessageChannels = trackMessageChannels();
  try {
    await act(async () => {
      pageSize.focus();
      fireEvent.keyDown(pageSize, { key: "Enter" });
    });
    const option = await screen.findByText(value, {
      selector: ".ant-select-item-option-content",
    });
    await act(async () => {
      fireEvent.click(option);
    });
    await waitFor(() =>
      expect(pageSize).toHaveAttribute("aria-expanded", "false"),
    );
  } finally {
    releaseMessageChannels();
  }
}

function client() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: Infinity,
        gcTime: Infinity,
        experimental_prefetchInRender: true,
      },
      mutations: { retry: false, gcTime: Infinity },
    },
  });
  queryClients.push(queryClient);
  return queryClient;
}

describe("Candidate Run bulk selection", () => {
  it("preserves a cross-page selection through a page-size change", async () => {
    const queryClient = client();
    const firstPage = [member("one")];
    const secondPage = [member("two")];
    seedRun(queryClient, {
      pages: [page(firstPage, "cursor-two"), page(secondPage)],
    });
    seedRun(queryClient, {
      pages: [page(firstPage, "cursor-two"), page(secondPage)],
      pageSize: 100,
    });
    renderView(queryClient);

    await screen.findByRole("checkbox", { name: memberCheckboxName("one") });
    fireEvent.click(rowCheckbox("one"));

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByRole("checkbox", {
      name: memberCheckboxName("two"),
    });
    fireEvent.click(rowCheckbox("two"));
    expect(screen.getByText("已选 2 人")).toBeInTheDocument();
    await choosePageSize(
      screen.getByRole("combobox", { name: "每页数量" }),
      "100",
    );
    await waitFor(() => expect(rowCheckbox("one")).toBeChecked());
    expect(screen.getByText("已选 2 人")).toBeInTheDocument();
  });

  it("keeps the rendered selection lifecycle scoped across A-B-A", () => {
    const oldMember = member("old");
    const view = renderSelectionLifecycle(oldMember);

    fireEvent.click(screen.getByRole("button", { name: "选择 old" }));
    expect(screen.getByLabelText("选择作用域")).toHaveTextContent(runId);
    expect(screen.getByLabelText("选择数量")).toHaveTextContent("1");

    const middleRunId = "run-middle";
    const middleMember = member("middle", middleRunId);
    view.rerender(
      <CandidateSelectionLifecycleHarness
        candidate={middleMember}
        currentRunId={middleRunId}
      />,
    );
    expect(screen.getByLabelText("选择作用域")).toHaveTextContent(middleRunId);
    expect(screen.getByLabelText("选择数量")).toHaveTextContent("0");

    const freshMember = member("fresh");
    view.rerender(
      <CandidateSelectionLifecycleHarness
        candidate={freshMember}
        currentRunId={runId}
      />,
    );
    expect(screen.getByLabelText("选择作用域")).toHaveTextContent(runId);
    expect(screen.getByLabelText("选择数量")).toHaveTextContent("0");
    fireEvent.click(screen.getByRole("button", { name: "选择 fresh" }));
    expect(screen.getByLabelText("选择数量")).toHaveTextContent("1");
  });

  it("gives same-influencer platform accounts distinct accessible selection names", async () => {
    const queryClient = client();
    const douyin = {
      ...member("douyin"),
      influencer: {
        ...member("douyin").influencer,
        display_name: "同一个达人",
      },
      platform_account: {
        ...member("douyin").platform_account,
        account_name: "同名账号",
        account_handle: null,
      },
    };
    const sameDisplayAccount = {
      ...member("douyin-duplicate"),
      influencer_id: douyin.influencer_id,
      influencer: { ...douyin.influencer },
      platform_account: {
        ...member("douyin-duplicate").platform_account,
        platform: "douyin",
        account_name: "同名账号",
        account_handle: null,
      },
    };
    seedRun(queryClient, {
      pages: [page([douyin, sameDisplayAccount])],
      matchCount: 2,
    });
    renderView(queryClient);

    await screen.findByRole("checkbox", {
      name: "选择 同一个达人：抖音 · 同名账号（候选记录 douyin）",
    });
    expect(
      screen.getByRole("checkbox", {
        name: "选择 同一个达人：抖音 · 同名账号（候选记录 douyin-duplicate）",
      }),
    ).toBeInTheDocument();
  });

  it("does not let a stale next-page completion overwrite a new result tab", async () => {
    const queryClient = client();
    const firstPage = [member("one")];
    const secondPage = [member("two")];
    const matchTabPage = [member("match-tab")];
    seedRun(queryClient, {
      pages: [page(firstPage, "cursor-two"), page(secondPage, "cursor-three")],
    });
    seedRun(queryClient, {
      pages: [page(matchTabPage)],
      result: "MATCH",
    });
    const nextPage = deferred<Response>();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => nextPage.promise);
    renderView(queryClient);

    await screen.findByRole("checkbox", {
      name: memberCheckboxName("one"),
    });
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByRole("checkbox", {
      name: memberCheckboxName("two"),
    });

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("tab", { name: "符合条件" }));
    await screen.findByRole("checkbox", {
      name: memberCheckboxName("match-tab"),
    });

    await act(async () => {
      nextPage.resolve(
        apiResponse({ items: [member("three")], next_cursor: null }),
      );
    });
    await waitFor(() => expect(queryClient.isFetching()).toBe(0));
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", {
          name: memberCheckboxName("match-tab"),
        }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("第 1 页")).toBeInTheDocument();
  });

  it("keeps ALL_MATCH selected and identifies both accounts after an ambiguity error", async () => {
    const queryClient = client();
    const rows = [member("one")];
    seedRun(queryClient, { pages: [page(rows)], matchCount: 2 });
    seedCampaignSelector(queryClient);
    const ambiguityResponse = deferred<Response>();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => ambiguityResponse.promise);
    renderView(queryClient);

    await screen.findByRole("button", {
      name: "全选 2 人",
    });
    fireEvent.click(screen.getByRole("button", { name: "全选 2 人" }));
    fireEvent.click(screen.getByRole("button", { name: "加入拓客活动" }));
    fireEvent.click(await screen.findByRole("radio", { name: /目标拓客活动/ }));
    const dialog = screen.getByRole("dialog");
    await act(async () => {
      fireEvent.click(
        within(dialog).getByRole("button", { name: "加入拓客活动" }),
      );
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await act(async () => {
      ambiguityResponse.resolve(
        apiResponse(null, 422, {
          code: "CANDIDATE_POOL_RUN_MEMBER_AMBIGUOUS",
          message: "same influencer has multiple accounts",
          details: {
            conflicting_influencer: {
              id: "influencer-conflict",
              display_name: "冲突达人",
            },
            conflicting_member_ids: ["member-douyin", "member-xiaohongshu"],
            conflicting_accounts: [
              {
                member_id: "member-douyin",
                platform: "douyin",
                account_name: "同名账号",
                account_handle: "douyin-handle",
              },
              {
                member_id: "member-xiaohongshu",
                platform: "xiaohongshu",
                account_name: "同名账号",
                account_handle: "xhs-handle",
              },
            ],
          },
        }),
      );
    });

    await screen.findByText(/达人“冲突达人”同时命中了多个平台账号/);
    await waitFor(() => expect(queryClient.isMutating()).toBe(0));
    await waitFor(() => expect(queryClient.isFetching()).toBe(0));
    expect(
      screen.getByText(
        "抖音 · 同名账号 @douyin-handle（候选记录 member-douyin）",
        {
          exact: false,
        },
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "小红书 · 同名账号 @xhs-handle（候选记录 member-xiaohongshu）",
        {
          exact: false,
        },
      ),
    ).toBeInTheDocument();
    expect(rowCheckbox("one")).toBeChecked();
    expect(screen.getAllByText("✓ 已全选 2 人")).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: "全选 2 人" }),
    ).not.toBeInTheDocument();
  });

  for (const lateCompletion of [
    {
      name: "success",
      response: () =>
        apiResponse({
          campaign_id: "campaign-selection",
          added_count: 1,
          restored_count: 0,
          already_active_count: 0,
        }),
    },
    {
      name: "not-found failure",
      response: () =>
        apiResponse(null, 422, {
          code: "CANDIDATE_POOL_RUN_MEMBER_NOT_FOUND",
          message: "selected member changed",
          details: null,
        }),
    },
  ]) {
    it(`does not let a late ${lateCompletion.name} close or clear a new run selection`, async () => {
      const queryClient = client();
      const oldRows = [member("old")];
      seedRun(queryClient, { pages: [page(oldRows)], matchCount: 1 });
      seedCampaignSelector(queryClient);
      const oldMutation = deferred<Response>();
      const fetchMock = vi
        .spyOn(globalThis, "fetch")
        .mockImplementation(() => oldMutation.promise);
      const view = renderView(queryClient);

      await screen.findByRole("checkbox", {
        name: memberCheckboxName("old"),
      });
      fireEvent.click(rowCheckbox("old"));
      fireEvent.click(screen.getByRole("button", { name: "加入拓客活动" }));
      fireEvent.click(
        await screen.findByRole("radio", { name: /目标拓客活动/ }),
      );
      fireEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name: "加入拓客活动",
        }),
      );
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

      const nextRunId = "run-late-next";
      const nextRows = [member("next", nextRunId)];
      seedRun(queryClient, {
        id: nextRunId,
        pages: [page(nextRows)],
        matchCount: 1,
      });
      rerenderView(view, queryClient, nextRunId);

      await screen.findByRole("checkbox", {
        name: memberCheckboxName("next"),
      });
      fireEvent.click(rowCheckbox("next"));
      fireEvent.click(screen.getByRole("button", { name: "加入拓客活动" }));
      await screen.findByRole("dialog");

      await act(async () => {
        oldMutation.resolve(lateCompletion.response());
      });
      await waitFor(() => expect(queryClient.isMutating()).toBe(0));
      await waitFor(() => expect(queryClient.isFetching()).toBe(0));
      expect(rowCheckbox("next")).toBeChecked();
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(screen.getAllByText("已选 1 人")).not.toHaveLength(0);
    });
  }
});
