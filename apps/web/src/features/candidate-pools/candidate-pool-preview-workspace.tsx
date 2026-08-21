"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Select, Typography } from "antd";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";

import { CandidatePoolDetailView } from "./candidate-pool-detail-view";
import { CandidatePoolListView } from "./candidate-pool-list-view";
import { CandidateRunDetailView } from "./candidate-run-detail-view";
import { candidatePoolQueryKeys } from "./queries";
import {
  PREVIEW_CAMPAIGNS,
  PREVIEW_MEMBERS,
  PREVIEW_POLICIES,
  PREVIEW_POOL_IDS,
  PREVIEW_POOL_PAGE,
  PREVIEW_POOLS,
  PREVIEW_RUN_IDS,
  PREVIEW_RUN_PAGES,
  PREVIEW_RUNS,
} from "./preview-fixtures";

const { Text } = Typography;

export const PREVIEW_SCENE_OPTIONS = [
  ["list", "候选池列表"],
  ["basic", "基本信息"],
  ["policies", "规则历史"],
  ["seller-policy", "SELLER 筛选规则"],
  ["buyer-policy", "BUYER 筛选规则"],
  ["runs", "生成记录"],
  ["pending", "等待生成"],
  ["running", "正在生成"],
  ["completed", "已完成"],
  ["failed", "生成失败"],
  ["seller-evidence", "SELLER 判断依据"],
  ["buyer-evidence", "BUYER 判断依据"],
  ["historical-identity", "停用达人和停用账号"],
  ["selected", "已选择候选达人"],
  ["multi-account", "同达人多账号限制"],
  ["campaign-selector", "加入拓客活动"],
  ["empty", "暂无候选结果"],
  ["load-failed", "加载失败"],
  ["viewer", "Viewer 只读"],
] as const;

type Scene = (typeof PREVIEW_SCENE_OPTIONS)[number][0];

function parseScene(value: string | null): Scene {
  return PREVIEW_SCENE_OPTIONS.some(([key]) => key === value)
    ? (value as Scene)
    : "list";
}

function infiniteData<T>(items: T[], next_cursor: string | null = null) {
  return { pages: [{ items, next_cursor }], pageParams: [null] };
}

function seedPreviewQueries(client: QueryClient) {
  client.setQueryData(
    candidatePoolQueryKeys.lists,
    infiniteData(PREVIEW_POOL_PAGE.items, PREVIEW_POOL_PAGE.next_cursor),
  );
  for (const pool of PREVIEW_POOLS) {
    client.setQueryData(candidatePoolQueryKeys.detail(pool.id), pool);
    client.setQueryData(
      candidatePoolQueryKeys.policies(pool.id),
      PREVIEW_POLICIES[pool.id] ?? [],
    );
    client.setQueryData(
      candidatePoolQueryKeys.runs(pool.id),
      infiniteData(PREVIEW_RUN_PAGES[pool.id]?.items ?? []),
    );
  }
  for (const run of PREVIEW_RUNS) {
    client.setQueryData(candidatePoolQueryKeys.run(run.pool_id, run.id), run);
    for (const result of [undefined, "MATCH", "UNKNOWN"] as const) {
      const items = PREVIEW_MEMBERS.filter(
        (member) =>
          member.run_id === run.id && (!result || member.result === result),
      );
      client.setQueryData(
        candidatePoolQueryKeys.members(run.pool_id, run.id, result),
        infiniteData(items, "preview-members-next"),
      );
    }
  }
  client.setQueryData(
    ["candidate-pools", "campaign-selector"],
    infiniteData(PREVIEW_CAMPAIGNS, "preview-campaigns-next"),
  );
}

function selectedMembers(ids: string[]) {
  return PREVIEW_MEMBERS.filter((member) => ids.includes(member.id));
}

function PreviewContent({ scene }: { scene: Scene }) {
  if (scene === "list") return <CandidatePoolListView previewMode />;
  if (scene === "empty")
    return (
      <CandidateRunDetailView
        key={scene}
        poolId={PREVIEW_POOL_IDS.seller}
        runId={PREVIEW_RUN_IDS.empty}
        role="manager"
        hasSelectedOperator
        previewMode
      />
    );
  if (scene === "load-failed")
    return (
      <CandidateRunDetailView
        key={scene}
        poolId={PREVIEW_POOL_IDS.seller}
        runId={PREVIEW_RUN_IDS.completed}
        role="manager"
        hasSelectedOperator
        previewMode
        previewState="error"
      />
    );

  if (
    ["basic", "policies", "seller-policy", "buyer-policy", "runs"].includes(
      scene,
    )
  ) {
    const buyer = scene === "buyer-policy";
    const poolId = buyer ? PREVIEW_POOL_IDS.buyer : PREVIEW_POOL_IDS.seller;
    const tab =
      scene === "basic" ? "basic" : scene === "runs" ? "runs" : "policies";
    return (
      <CandidatePoolDetailView
        key={scene}
        poolId={poolId}
        role="manager"
        hasSelectedOperator
        previewMode
        previewTab={tab}
      />
    );
  }

  const viewer = scene === "viewer";
  const buyerEvidence = scene === "buyer-evidence";
  const evidenceId = buyerEvidence
    ? "member-buyer-category-mismatch"
    : scene === "historical-identity"
      ? "member-disabled-influencer"
      : "member-active-match";
  const previewEvidence =
    PREVIEW_MEMBERS.find((member) => member.id === evidenceId) ?? null;
  const selected =
    scene === "selected" || scene === "campaign-selector"
      ? selectedMembers(
          scene === "campaign-selector"
            ? ["member-active-match", "member-unknown"]
            : ["member-active-match"],
        )
      : scene === "multi-account"
        ? selectedMembers(["member-lin-xia-selected"])
        : [];
  const runId = buyerEvidence
    ? PREVIEW_RUN_IDS.buyerCompleted
    : PREVIEW_RUN_IDS.completed;
  const statusRunId =
    scene === "pending"
      ? PREVIEW_RUN_IDS.pending
      : scene === "running"
        ? PREVIEW_RUN_IDS.running
        : scene === "failed"
          ? PREVIEW_RUN_IDS.failed
          : runId;
  return (
    <CandidateRunDetailView
      key={scene}
      poolId={buyerEvidence ? PREVIEW_POOL_IDS.buyer : PREVIEW_POOL_IDS.seller}
      runId={statusRunId}
      role={viewer ? "viewer" : "manager"}
      hasSelectedOperator={!viewer}
      previewMode
      previewSelectedMembers={selected}
      previewEvidence={
        scene === "seller-evidence" || buyerEvidence ? previewEvidence : null
      }
      previewModalOpen={scene === "campaign-selector"}
    />
  );
}

function PreviewFrame() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const scene = parseScene(searchParams.get("scene"));
  const [queryClient] = useState(() => {
    const client = new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: Infinity,
          gcTime: Infinity,
          refetchOnWindowFocus: false,
          retry: false,
        },
      },
    });
    seedPreviewQueries(client);
    return client;
  });
  const label = useMemo(
    () =>
      PREVIEW_SCENE_OPTIONS.find(([key]) => key === scene)?.[1] ?? "候选池列表",
    [scene],
  );
  return (
    <QueryClientProvider client={queryClient}>
      <AppShell
        title="候选池 · 开发预览"
        description="仅用于核对已冻结的 Candidate Pool 视觉基线。"
        department="开发预览"
        operator="内存 fixture"
        role="仅供开发"
        onLogout={() => undefined}
      >
        <div className="candidate-pool-preview-workspace">
          <div className="candidate-pool-preview-toolbar">
            <span className="candidate-pool-preview-toolbar-label">场景</span>
            <Select
              aria-label="候选池预览场景"
              value={scene}
              options={PREVIEW_SCENE_OPTIONS.map(([value, optionLabel]) => ({
                value,
                label: optionLabel,
              }))}
              onChange={(value: Scene) => {
                router.replace(
                  `/dev-ui-preview/candidate-pools?scene=${value}`,
                );
              }}
            />
            <Text type="secondary">{label}</Text>
          </div>
          <PreviewContent scene={scene} />
        </div>
      </AppShell>
    </QueryClientProvider>
  );
}

export function CandidatePoolPreviewWorkspace() {
  return <PreviewFrame />;
}
