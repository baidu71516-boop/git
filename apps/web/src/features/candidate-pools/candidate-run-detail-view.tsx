"use client";

import { ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Drawer,
  Modal,
  Radio,
  Select,
  Skeleton,
  Space,
  Table,
  Tabs,
  Typography,
  message,
} from "antd";
import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { AppEmpty } from "@/components/ui/app-empty";
import { StatusBadge } from "@/components/ui/status-badge";
import { fetchCampaignPage } from "@/features/campaigns/api";
import { campaignStatusPresentation } from "@/features/campaigns/formatters";
import type { Campaign } from "@/features/campaigns/types";
import { platformLabel } from "@/features/influencers/formatters";
import { ApiClientError } from "@/lib/api/client";

import {
  CANDIDATE_MEMBER_PAGE_LIMIT,
  CANDIDATE_MEMBER_PAGE_SIZES,
} from "./api";
import {
  candidateSelectionIdentitiesEqual,
  candidateSelectionActions,
  candidateSelectionCount,
  candidateSelectionPageState,
  candidateSelectionPayload,
  candidateSelectionUnknownCount,
  candidateSelectionWouldExceedSelectionLimit,
  isCandidateMemberSelectable,
  isCandidateMemberSelected,
  type CandidateSelectionScope,
  type CandidateSelectionState,
  useCandidateSelectionLifecycle,
} from "./candidate-selection";
import {
  candidateDateTime,
  candidateReasonLabel,
  candidateResultPresentation,
  candidateRunFailureMessage,
  candidateRunStatus,
  isAmbiguousMutation,
  mutationErrorMessage,
  policyType,
} from "./formatters";
import { PolicyDefinition } from "./candidate-pool-detail-view";
import {
  useAddCandidatesToCampaignMutation,
  useCandidateMembers,
  useCandidatePool,
  useCandidatePolicies,
  useCandidateRun,
  useCreateCandidateRunMutation,
} from "./queries";
import { SellerRuleBuilder } from "./seller-rule-builder";
import { isAuthorableSellerTargetingPolicy } from "./types";
import type {
  CandidateCampaignAddResult,
  CandidateCampaignSelection,
  CandidateMember,
  CandidateMemberPageSize,
  CandidatePoolRole,
  SellerTargetingPolicy,
  TargetingPolicy,
} from "./types";

const { Text } = Typography;
const attemptKey = () =>
  globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

type CandidateMemberNavigationScope = {
  candidatePoolId: string;
  runId: string;
  filter: "all" | "MATCH" | "UNKNOWN";
  pageSize: CandidateMemberPageSize;
};

function candidateMemberNavigationScopesEqual(
  left: CandidateMemberNavigationScope,
  right: CandidateMemberNavigationScope,
): boolean {
  return (
    left.candidatePoolId === right.candidatePoolId &&
    left.runId === right.runId &&
    left.filter === right.filter &&
    left.pageSize === right.pageSize
  );
}

function memberSelectionLabel(member: CandidateMember): string {
  const account = member.platform_account;
  const handle = account.account_handle ? ` @${account.account_handle}` : "";
  return `选择 ${member.influencer.display_name}：${platformLabel(account.platform)} · ${account.account_name}${handle}（候选记录 ${member.id}）`;
}

function selectionLimitMessage(selection: CandidateSelectionState): string {
  return selection.mode === "ALL_MATCH"
    ? "ALL_MATCH 最多可排除 10,000 位达人。请先恢复部分已排除的达人后再继续。"
    : "一次最多可选择 10,000 位达人。请先取消部分已选达人后再继续。";
}

function selectedCandidateLabel(
  selection: CandidateSelectionState,
  selectedCount: number,
): string {
  if (
    selection.mode === "ALL_MATCH" &&
    selectedCount === selection.matchCount
  ) {
    return `✓ 已全选 ${selectedCount} 人`;
  }
  return `已选 ${selectedCount} 人`;
}

function ambiguityErrorMessage(error: unknown): string | null {
  if (
    !(error instanceof ApiClientError) ||
    error.code !== "CANDIDATE_POOL_RUN_MEMBER_AMBIGUOUS"
  ) {
    return null;
  }
  const details =
    error.details && typeof error.details === "object"
      ? (error.details as Record<string, unknown>)
      : {};
  const conflict =
    details.conflict && typeof details.conflict === "object"
      ? (details.conflict as Record<string, unknown>)
      : details;
  const conflictingInfluencer =
    conflict.conflicting_influencer &&
    typeof conflict.conflicting_influencer === "object"
      ? (conflict.conflicting_influencer as Record<string, unknown>)
      : conflict;
  const influencer =
    typeof conflictingInfluencer.display_name === "string"
      ? conflictingInfluencer.display_name
      : typeof conflict.influencer_display_name === "string"
        ? conflict.influencer_display_name
        : null;
  const accounts = Array.isArray(conflict.conflicting_accounts)
    ? conflict.conflicting_accounts
        .map((item) => {
          if (!item || typeof item !== "object") return null;
          const account = item as Record<string, unknown>;
          const accountName =
            typeof account.account_name === "string"
              ? account.account_name
              : "未命名账号";
          const platform =
            typeof account.platform === "string"
              ? platformLabel(account.platform)
              : "未知平台";
          const handle =
            typeof account.account_handle === "string" && account.account_handle
              ? ` @${account.account_handle}`
              : "";
          const memberId =
            typeof account.member_id === "string" && account.member_id
              ? `（候选记录 ${account.member_id}）`
              : "";
          return `${platform} · ${accountName}${handle}${memberId}`;
        })
        .filter((item): item is string => item !== null)
    : Array.isArray(conflict.account_names)
      ? conflict.account_names.filter(
          (item): item is string => typeof item === "string",
        )
      : [];
  if (influencer && accounts.length > 0) {
    return `达人“${influencer}”同时命中了多个平台账号（${accounts.join("、")}）。请在候选结果中取消其中一个账号后重试。`;
  }
  if (influencer) {
    return `达人“${influencer}”同时命中了多个平台账号。请在候选结果中取消其中一个账号后重试。`;
  }
  return "同一达人命中了多个平台账号。请在候选结果中取消其中一个账号后重试。";
}

export function CandidateCampaignSuccessFeedback({
  campaignId,
  result,
}: {
  campaignId: string;
  result: CandidateCampaignAddResult;
}) {
  return (
    <span>
      <span>
        候选达人已加入拓客活动：新增 {result.added_count} · 重新加入{" "}
        {result.restored_count} · 已在活动中 {result.already_active_count}
        {result.already_active_count > 0 ? "。已在活动中的达人未做修改。" : ""}
      </span>
      <br />
      <Link href={`/campaigns/${encodeURIComponent(campaignId)}?tab=members`}>
        查看拓客活动
      </Link>
    </span>
  );
}

function Identity({ member }: { member: CandidateMember }) {
  const disabled = member.influencer.status === "disabled";
  return (
    <span>
      {disabled ? (
        member.influencer.display_name
      ) : (
        <Link href={`/influencers/${encodeURIComponent(member.influencer.id)}`}>
          {member.influencer.display_name}
        </Link>
      )}
      {disabled ? <Text type="secondary"> · 已停用</Text> : null}
    </span>
  );
}
function Account({ member }: { member: CandidateMember }) {
  const account = member.platform_account;
  return (
    <span className="candidate-account" title={`候选记录 ${member.id}`}>
      <span>
        {platformLabel(account.platform)} · {account.account_name}
      </span>
      {account.account_handle || !account.is_active ? (
        <span className="candidate-account-meta">
          {account.account_handle ? `@${account.account_handle}` : null}
          {!account.is_active
            ? `${account.account_handle ? " · " : ""}账号已停用`
            : null}
        </span>
      ) : null}
    </span>
  );
}

function safeString(value: unknown): string | null {
  return typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
    ? String(value)
    : null;
}
function safeList(value: unknown): string {
  return Array.isArray(value)
    ? value
        .map(safeString)
        .filter((item): item is string => item !== null)
        .join("、")
    : "—";
}

function criterionConfiguredValue(
  value: unknown,
  criterion: string | null,
): string {
  const item =
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  if (criterion === "content_activity" || criterion === "long_inactivity") {
    const days = safeString(item.minimum_inactive_days);
    return days
      ? `${criterion === "long_inactivity" ? "长期断更" : "断更"}不少于 ${days} 天`
      : "—";
  }
  return safeList(item.allowed ?? item.minimum ?? item.maximum);
}

export function criterionObservedValue(
  value: unknown,
  criterion: string | null,
): string {
  const item =
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  if (criterion === "content_activity") {
    const inactiveDays = safeString(item.inactive_days);
    if (inactiveDays) return `已断更 ${inactiveDays} 天`;
    const publication = safeString(item.last_publication_at);
    if (publication) return `最后公开：${candidateDateTime(publication)}`;
  }
  if (criterion === "long_inactivity") {
    const source = safeString(item.source);
    if (source === "GREY_DOLPHIN") {
      if (safeString(item.notes_60d) === "0")
        return "灰豚粗略判断：近60天未检测到笔记";
      const notes7d = safeString(item.notes_7d);
      if (notes7d !== null && notes7d !== "0")
        return "灰豚粗略判断：近7天检测到笔记";
      return "断更状态：未知 · 来源：灰豚";
    }
    if (source === "TRUSTED_CONTENT_ACTIVITY") {
      const inactiveDays = safeString(item.inactive_days);
      if (inactiveDays) return `断更 ${inactiveDays} 天 · 来源：API验证`;
    }
    if (source === "HUITUN_DOUYIN_AWEME_LIST") {
      const precision = safeString(item.precision);
      const inactiveDays = safeString(item.inactive_days);
      if (precision === "EXACT" && inactiveDays)
        return `灰豚运行时观测：断更 ${inactiveDays} 天`;
      const lowerBoundDays = safeString(item.lower_bound_inactive_days);
      if (precision === "LOWER_BOUND" && lowerBoundDays)
        return `灰豚运行时观测：至少断更 ${lowerBoundDays} 天（已验证范围）`;
      return "断更状态：未知 · 灰豚运行时观测未形成可信结果";
    }
    return "断更状态：未知";
  }
  return safeString(item.value ?? item.status) ?? "—";
}

function EvidenceContent({
  member,
  policy,
}: {
  member: CandidateMember;
  policy?: TargetingPolicy;
}) {
  const evidence = member.redacted_evidence;
  const type =
    evidence.schema_version === 1 &&
    (evidence.policy_type === "SELLER_V1" ||
      evidence.policy_type === "BUYER_V1")
      ? evidence.policy_type
      : policy
        ? policyType(policy.definition)
        : null;
  if (!type)
    return <Text type="secondary">该判断依据版本暂不支持完整展示。</Text>;
  if (type === "SELLER_V1") {
    const criteria = Array.isArray(evidence.criteria)
      ? evidence.criteria
          .filter(
            (item): item is Record<string, unknown> =>
              Boolean(item) && typeof item === "object" && !Array.isArray(item),
          )
          .map((item, index) => ({
            ...item,
            __preview_key: `criterion-${index}`,
          }))
      : [];
    return (
      <Table<Record<string, unknown>>
        size="small"
        rowKey="__preview_key"
        pagination={false}
        columns={[
          {
            title: "条件",
            dataIndex: "criterion",
            render: (value: unknown) => safeString(value) ?? "—",
          },
          {
            title: "结果",
            dataIndex: "result",
            render: (value: unknown) => safeString(value) ?? "—",
          },
          {
            title: "配置",
            dataIndex: "configured",
            render: (value: unknown, record: Record<string, unknown>) =>
              criterionConfiguredValue(value, safeString(record.criterion)),
          },
          {
            title: "观察值",
            dataIndex: "observed",
            render: (value: unknown, record: Record<string, unknown>) =>
              criterionObservedValue(value, safeString(record.criterion)),
          },
          {
            title: "原因",
            dataIndex: "reason_code",
            render: (value: unknown) =>
              typeof value === "string"
                ? candidateReasonLabel(value)
                : "未知判断原因",
          },
        ]}
        dataSource={criteria}
      />
    );
  }
  const collection =
    evidence.collection_context &&
    typeof evidence.collection_context === "object" &&
    !Array.isArray(evidence.collection_context)
      ? (evidence.collection_context as Record<string, unknown>)
      : {};
  const creator =
    evidence.creator_classification &&
    typeof evidence.creator_classification === "object" &&
    !Array.isArray(evidence.creator_classification)
      ? (evidence.creator_classification as Record<string, unknown>)
      : {};
  return (
    <div>
      <div className="candidate-evidence-row">
        <Text type="secondary">分类版本</Text>
        <span>{safeString(evidence.taxonomy_version) ?? "—"}</span>
      </div>
      <div className="candidate-evidence-row">
        <Text type="secondary">采集上下文</Text>
        <span>
          {[
            safeString(collection.industry),
            safeString(collection.subdirection),
            safeList(collection.normalized_categories),
          ]
            .filter((value) => value && value !== "—")
            .join(" · ") || "—"}
        </span>
      </div>
      <div className="candidate-evidence-row">
        <Text type="secondary">账号分类</Text>
        <span>{safeList(creator.normalized_categories ?? creator.tags)}</span>
      </div>
      <div className="candidate-evidence-row">
        <Text type="secondary">判断原因</Text>
        <span>
          {typeof evidence.reason === "string"
            ? candidateReasonLabel(evidence.reason)
            : member.reason_codes.map(candidateReasonLabel).join("、")}
        </span>
      </div>
    </div>
  );
}

function CampaignSelector({
  open,
  selection,
  unknownCount,
  selectedLabel,
  onClose,
  onSuccess,
  onInvalidSelection,
  previewMode = false,
}: {
  open: boolean;
  selection: CandidateCampaignSelection;
  unknownCount: number;
  selectedLabel: string;
  onClose: () => void;
  onSuccess: () => boolean;
  onInvalidSelection: () => boolean;
  previewMode?: boolean;
}) {
  const campaigns = useInfiniteQuery({
    queryKey: ["candidate-pools", "campaign-selector"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => fetchCampaignPage(pageParam),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: open,
    retry: (count, error) =>
      !(
        error instanceof Error &&
        "status" in error &&
        Number((error as { status: unknown }).status) < 500
      ) && count < 1,
  });
  const mutation = useAddCandidatesToCampaignMutation();
  const [campaignId, setCampaignId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState<{ key: string; payload: string } | null>(
    null,
  );
  const [messageApi, holder] = message.useMessage();
  const items = useMemo(
    () => campaigns.data?.pages.flatMap((page) => page.items) ?? [],
    [campaigns.data],
  );
  async function submit() {
    if (!campaignId) return;
    if (previewMode) {
      onSuccess();
      return;
    }
    const selectedCampaignId = campaignId;
    const payload = JSON.stringify({ campaignId, selection });
    const current =
      retry?.payload === payload ? retry : { key: attemptKey(), payload };
    setError(null);
    try {
      const result = await mutation.mutateAsync({
        campaignId,
        selection,
        idempotencyKey: current.key,
      });
      setRetry(null);
      if (onSuccess()) {
        void messageApi.success(
          <CandidateCampaignSuccessFeedback
            campaignId={selectedCampaignId}
            result={result}
          />,
        );
      }
    } catch (caught) {
      const label =
        ambiguityErrorMessage(caught) ??
        mutationErrorMessage(caught, "加入拓客活动失败，请稍后重试。");
      setError(label);
      if (isAmbiguousMutation(caught)) setRetry(current);
      else if ((caught as { code?: string }).code === "CAMPAIGN_CLOSED") {
        setCampaignId(null);
        void campaigns.refetch();
      } else if (
        (caught as { code?: string }).code ===
        "CANDIDATE_POOL_RUN_MEMBER_NOT_FOUND"
      ) {
        onInvalidSelection();
      }
    }
  }
  const selectable = items.some((campaign) =>
    ["DRAFT", "ACTIVE", "PAUSED"].includes(campaign.status),
  );
  return (
    <Modal
      title="加入拓客活动"
      open={open}
      width={680}
      okText="加入拓客活动"
      cancelText="取消"
      confirmLoading={mutation.isPending}
      okButtonProps={{ disabled: !campaignId }}
      onCancel={() => !mutation.isPending && onClose()}
      onOk={() => void submit()}
    >
      {holder}
      <Text>{selectedLabel}</Text>
      {unknownCount ? (
        <Alert
          type="warning"
          showIcon
          title={`其中 ${unknownCount} 位候选达人信息不足，请确认后再加入。`}
          className="campaign-modal-alert"
        />
      ) : null}
      {error ? (
        <Alert
          type="error"
          showIcon
          title={error}
          className="campaign-modal-alert"
        />
      ) : null}
      {campaigns.isPending ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : campaigns.isError ? (
        <Alert
          type="error"
          showIcon
          title="拓客活动加载失败"
          action={
            <Button onClick={() => void campaigns.refetch()}>重新加载</Button>
          }
        />
      ) : !selectable ? (
        <div className="campaign-empty-state">
          <AppEmpty description="暂无可加入的拓客活动" />
          <Text type="secondary">当前没有状态允许加入候选达人的活动。</Text>
        </div>
      ) : (
        <>
          <Radio.Group
            value={campaignId}
            onChange={(event) => {
              setCampaignId(event.target.value);
              setRetry(null);
            }}
            className="candidate-campaign-list"
          >
            {items.map((campaign: Campaign) => {
              const status = campaignStatusPresentation(campaign.status);
              const disabled = campaign.status === "CLOSED";
              return (
                <Radio
                  key={campaign.id}
                  value={campaign.id}
                  disabled={disabled}
                  className="candidate-campaign-option"
                >
                  <span>{campaign.name}</span>
                  <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                  <Text type="secondary">负责人：{campaign.owner.name}</Text>
                </Radio>
              );
            })}
          </Radio.Group>
          {campaigns.hasNextPage ? (
            <div className="campaign-pagination-footer">
              <Button
                icon={<ReloadOutlined aria-hidden="true" />}
                loading={previewMode ? false : campaigns.isFetchingNextPage}
                onClick={() => {
                  if (!previewMode) void campaigns.fetchNextPage();
                }}
              >
                加载更多活动
              </Button>
            </div>
          ) : null}
        </>
      )}
    </Modal>
  );
}

export function CandidateRunDetailView({
  poolId,
  runId,
  role,
  hasSelectedOperator,
  previewMode = false,
  previewSelectedMembers,
  previewEvidence,
  previewModalOpen = false,
  previewSelectionAttempted = false,
  previewState,
}: {
  poolId: string;
  runId: string;
  role: CandidatePoolRole;
  hasSelectedOperator: boolean;
  previewMode?: boolean;
  previewSelectedMembers?: CandidateMember[];
  previewEvidence?: CandidateMember | null;
  previewModalOpen?: boolean;
  previewSelectionAttempted?: boolean;
  previewState?: "error";
}) {
  const activePolling = !previewMode;
  const router = useRouter();
  const runQuery = useCandidateRun(poolId, runId, activePolling);
  const policies = useCandidatePolicies(poolId, Boolean(runQuery.data));
  const rerunMutation = useCreateCandidateRunMutation();
  const [filter, setFilter] = useState<"all" | "MATCH" | "UNKNOWN">("all");
  const [pageSize, setPageSize] = useState<CandidateMemberPageSize>(
    CANDIDATE_MEMBER_PAGE_LIMIT,
  );
  const [currentPageIndex, setCurrentPageIndex] = useState(0);
  const [pageNavigationScope, setPageNavigationScope] = useState({
    candidatePoolId: poolId,
    filter: "all" as "all" | "MATCH" | "UNKNOWN",
    pageSize: CANDIDATE_MEMBER_PAGE_LIMIT as CandidateMemberPageSize,
    runId,
  });
  const activeNavigationScope: CandidateMemberNavigationScope = {
    candidatePoolId: poolId,
    runId,
    filter,
    pageSize,
  };
  const activeSelectionScope: CandidateSelectionScope = {
    candidatePoolId: poolId,
    runId,
  };
  const navigationIntentRef = useRef(activeNavigationScope);
  const navigationRequestRef = useRef(0);
  useLayoutEffect(() => {
    const nextScope: CandidateMemberNavigationScope = {
      candidatePoolId: poolId,
      runId,
      filter,
      pageSize,
    };
    if (
      !candidateMemberNavigationScopesEqual(
        navigationIntentRef.current,
        nextScope,
      )
    ) {
      navigationIntentRef.current = nextScope;
      navigationRequestRef.current += 1;
    }
  }, [filter, pageSize, poolId, runId]);
  const {
    dispatchSelection,
    selection,
    selectionGeneration,
    selectionIdentityRef,
  } = useCandidateSelectionLifecycle({
    scope: activeSelectionScope,
    selected: previewSelectedMembers,
  });
  const membersQuery = useCandidateMembers(
    poolId,
    runId,
    filter === "all" ? undefined : filter,
    runQuery.data?.status === "COMPLETED",
    pageSize,
  );
  const [evidence, setEvidence] = useState<CandidateMember | null>(
    previewMode ? null : (previewEvidence ?? null),
  );
  const [ruleOpen, setRuleOpen] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [adjustError, setAdjustError] = useState<string | null>(null);
  const [adjustRetry, setAdjustRetry] = useState<{
    key: string;
    payload: string;
  } | null>(null);
  const poolQuery = useCandidatePool(poolId, adjusting);
  const [modalOpen, setModalOpen] = useState(
    previewMode ? false : previewModalOpen,
  );
  const [modalScope, setModalScope] = useState({
    candidatePoolId: poolId,
    runId,
    generation: 0,
  });
  const [messageApi, holder] = message.useMessage();
  useEffect(() => {
    if (previewMode) return;
    function visible() {
      if (!document.hidden) void runQuery.refetch();
    }
    document.addEventListener("visibilitychange", visible);
    return () => document.removeEventListener("visibilitychange", visible);
  }, [previewMode, runQuery]);
  useEffect(() => {
    if (!previewMode || !previewModalOpen) return;
    const timer = window.setTimeout(() => {
      setModalScope({
        candidatePoolId: poolId,
        runId,
        generation: selectionIdentityRef.current.generation,
      });
      setModalOpen(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    poolId,
    previewMode,
    previewModalOpen,
    runId,
    selectionGeneration,
    selectionIdentityRef,
  ]);
  useEffect(() => {
    if (!previewMode || !previewEvidence) return;
    const timer = window.setTimeout(() => setEvidence(previewEvidence), 0);
    return () => window.clearTimeout(timer);
  }, [previewEvidence, previewMode]);
  useEffect(() => {
    if (!previewMode || !previewSelectionAttempted) return;
    const timer = window.setTimeout(() => {
      void messageApi.warning(
        "同一达人只能选择一个平台账号，请先取消已选账号。",
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [messageApi, previewMode, previewSelectionAttempted]);
  const readError = Boolean(runQuery.isError && runQuery.data);
  const run = runQuery.data;
  const policy = policies.data?.find((item) => item.id === run?.policy_id);
  const memberPages = useMemo(
    () => membersQuery.data?.pages ?? [],
    [membersQuery.data],
  );
  const visiblePageIndex = candidateMemberNavigationScopesEqual(
    pageNavigationScope,
    activeNavigationScope,
  )
    ? currentPageIndex
    : 0;
  const members = memberPages[visiblePageIndex]?.items ?? [];
  const pageSelection = candidateSelectionPageState(selection, members);
  const selectedCount = candidateSelectionCount(selection);
  const selectedLabel = selectedCandidateLabel(selection, selectedCount);
  const selectionPayload = candidateSelectionPayload(selection);
  const unknownCount = candidateSelectionUnknownCount(selection);
  const scopedModalOpen =
    modalOpen &&
    modalScope.candidatePoolId === poolId &&
    modalScope.runId === runId &&
    modalScope.generation === selectionGeneration;
  const canWrite = role !== "viewer" && hasSelectedOperator;
  const pool = poolQuery.data;
  const currentSellerPolicy =
    policy !== undefined &&
    isAuthorableSellerTargetingPolicy(policy.definition) &&
    pool?.current_policy_id === policy.id &&
    pool.status === "ACTIVE";
  async function adjustAndRerun(nextPolicy: SellerTargetingPolicy) {
    if (!pool || !policy || !currentSellerPolicy || previewMode) return;
    const request = {
      base_policy_id: policy.id,
      expected_pool_version: pool.version,
      policy: nextPolicy,
    };
    const serialized = JSON.stringify(request);
    const attempt =
      adjustRetry?.payload === serialized
        ? adjustRetry
        : { key: attemptKey(), payload: serialized };
    setAdjustError(null);
    try {
      const nextRun = await rerunMutation.mutateAsync({
        poolId,
        idempotencyKey: attempt.key,
        payload: request,
      });
      setAdjustRetry(null);
      setAdjusting(false);
      void messageApi.success("新规则版本已保存，候选结果已开始生成");
      router.push(
        `/candidate-pools/${encodeURIComponent(poolId)}/runs/${encodeURIComponent(nextRun.id)}`,
      );
    } catch (caught) {
      if (
        caught instanceof ApiClientError &&
        caught.code === "VERSION_CONFLICT"
      ) {
        setAdjustRetry(null);
        setAdjustError("规则已被其他人更新。请刷新后在当前规则上重新调整。");
        void poolQuery.refetch();
      } else if (isAmbiguousMutation(caught)) {
        setAdjustRetry(attempt);
        setAdjustError("保存结果暂时无法确认。你可以重试本次提交。");
      } else {
        setAdjustRetry(null);
        setAdjustError(
          mutationErrorMessage(caught, "保存规则并重新运行失败，请稍后重试。"),
        );
      }
    }
  }
  function updateMemberPageNavigation(
    nextScope: CandidateMemberNavigationScope,
    nextPageIndex: number,
  ): number {
    navigationIntentRef.current = nextScope;
    navigationRequestRef.current += 1;
    setPageNavigationScope(nextScope);
    setCurrentPageIndex(nextPageIndex);
    return navigationRequestRef.current;
  }
  function changeFilter(next: "all" | "MATCH" | "UNKNOWN") {
    setFilter(next);
    updateMemberPageNavigation(
      { candidatePoolId: poolId, filter: next, pageSize, runId },
      0,
    );
  }
  function toggle(member: CandidateMember, checked: boolean) {
    if (
      checked &&
      selection.mode === "EXPLICIT" &&
      Object.values(selection.selectedById).some(
        (item) =>
          item.influencer_id === member.influencer_id && item.id !== member.id,
      )
    ) {
      void messageApi.warning(
        "同一达人只能选择一个平台账号，请先取消已选账号。",
      );
      return;
    }
    if (
      candidateSelectionWouldExceedSelectionLimit(selection, [member], checked)
    ) {
      void messageApi.warning(selectionLimitMessage(selection));
      return;
    }
    dispatchSelection(candidateSelectionActions.toggleMember(member, checked));
  }
  function selectCurrentPage() {
    if (candidateSelectionWouldExceedSelectionLimit(selection, members, true)) {
      void messageApi.warning(selectionLimitMessage(selection));
      return;
    }
    dispatchSelection(candidateSelectionActions.selectPage(members));
    if (pageSelection.blockedCount > 0) {
      void messageApi.warning(
        "本页有同一达人对应多个平台账号，请逐个选择其中一个账号。",
      );
    }
  }
  function deselectCurrentPage() {
    if (
      candidateSelectionWouldExceedSelectionLimit(selection, members, false)
    ) {
      void messageApi.warning(selectionLimitMessage(selection));
      return;
    }
    dispatchSelection(candidateSelectionActions.deselectPage(members));
  }
  async function nextMemberPage() {
    const requestScope = activeNavigationScope;
    const nextPageIndex = visiblePageIndex + 1;
    if (visiblePageIndex < memberPages.length - 1) {
      updateMemberPageNavigation(requestScope, nextPageIndex);
      return;
    }
    if (!membersQuery.hasNextPage || membersQuery.isFetchingNextPage) return;
    const requestId = updateMemberPageNavigation(
      requestScope,
      visiblePageIndex,
    );
    const next = await membersQuery.fetchNextPage();
    if (
      !next.isError &&
      navigationRequestRef.current === requestId &&
      candidateMemberNavigationScopesEqual(
        navigationIntentRef.current,
        requestScope,
      )
    ) {
      setCurrentPageIndex(nextPageIndex);
    }
  }
  function previousMemberPage() {
    updateMemberPageNavigation(
      activeNavigationScope,
      Math.max(0, visiblePageIndex - 1),
    );
  }
  function openCampaignSelector() {
    setModalScope({
      candidatePoolId: poolId,
      runId,
      generation: selectionIdentityRef.current.generation,
    });
    setModalOpen(true);
  }
  function finishCampaignSelection(
    scope: CandidateSelectionScope,
    generation: number,
  ): boolean {
    const currentSelection = selectionIdentityRef.current;
    if (
      !candidateSelectionIdentitiesEqual(currentSelection, {
        scope,
        generation,
      })
    ) {
      return false;
    }
    setModalOpen(false);
    dispatchSelection(candidateSelectionActions.clearAll(scope));
    return true;
  }
  if (runQuery.isPending)
    return (
      <section className="campaign-workspace">
        <Skeleton active paragraph={{ rows: 7 }} />
      </section>
    );
  if (runQuery.isError && !run)
    return (
      <section className="campaign-workspace">
        <Alert
          type="error"
          showIcon
          title="状态更新失败"
          description="请稍后重试。"
          action={
            <Button onClick={() => void runQuery.refetch()}>重新加载</Button>
          }
        />
      </section>
    );
  if (previewState === "error") {
    return (
      <section className="candidate-run-detail-workspace campaign-workspace">
        <Alert
          type="error"
          showIcon
          title="候选结果加载失败"
          description="请稍后重试。"
          action={<Button onClick={() => undefined}>重新加载</Button>}
        />
      </section>
    );
  }
  if (!run) return null;
  const status = candidateRunStatus(run.status);
  const terminal = run.status === "COMPLETED" || run.status === "FAILED";
  const columns = [
    ...(canWrite
      ? [
          {
            title: (
              <Checkbox
                aria-label="选择本页候选达人"
                checked={pageSelection.checked}
                disabled={!pageSelection.eligibleCount}
                indeterminate={pageSelection.indeterminate}
                onChange={(event) => {
                  if (event.target.checked) selectCurrentPage();
                  else deselectCurrentPage();
                }}
              />
            ),
            key: "select",
            render: (_: unknown, member: CandidateMember) => (
              <Checkbox
                aria-label={memberSelectionLabel(member)}
                checked={isCandidateMemberSelected(selection, member)}
                disabled={!isCandidateMemberSelectable(selection, member)}
                onChange={(event) => toggle(member, event.target.checked)}
              />
            ),
          },
        ]
      : []),
    {
      title: "候选达人",
      key: "influencer",
      render: (_: unknown, member: CandidateMember) => (
        <Identity member={member} />
      ),
    },
    {
      title: "平台账号",
      key: "account",
      render: (_: unknown, member: CandidateMember) => (
        <Account member={member} />
      ),
    },
    {
      title: "结果",
      key: "result",
      render: (_: unknown, member: CandidateMember) => {
        const presentation = candidateResultPresentation(member.result);
        return (
          <StatusBadge tone={presentation.tone}>
            {presentation.label}
          </StatusBadge>
        );
      },
    },
    {
      title: "判断原因",
      key: "reason",
      render: (_: unknown, member: CandidateMember) =>
        member.reason_codes.map(candidateReasonLabel).join("、"),
    },
    {
      title: "判断依据",
      key: "evidence",
      render: (_: unknown, member: CandidateMember) => (
        <Button type="link" onClick={() => setEvidence(member)}>
          查看依据
        </Button>
      ),
    },
  ];
  const emptyLabel =
    filter === "MATCH"
      ? "暂无符合条件的候选达人"
      : filter === "UNKNOWN"
        ? "暂无信息不足的候选达人"
        : "暂无候选结果";
  return (
    <section
      className="candidate-run-detail-workspace campaign-workspace"
      aria-label="候选结果"
    >
      {holder}
      <div>
        <h2 className="page-title">
          候选结果 <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
        </h2>
        <Text>数据基准时间：{candidateDateTime(run.as_of)}</Text>
        <Text type="secondary">
          　创建时间：{candidateDateTime(run.created_at)}
        </Text>
        {policy ? (
          <>
            <Text type="secondary">　规则版本：版本 {policy.version}</Text>
            <Button type="link" onClick={() => setRuleOpen(true)}>
              查看规则
            </Button>
            {canWrite &&
            isAuthorableSellerTargetingPolicy(policy.definition) ? (
              <Button type="link" onClick={() => setAdjusting(true)}>
                调整规则并重新运行
              </Button>
            ) : null}
          </>
        ) : null}
      </div>
      {readError ? (
        <Alert
          type="warning"
          showIcon
          title="状态更新失败"
          action={
            <Button onClick={() => void runQuery.refetch()}>重新加载</Button>
          }
        />
      ) : null}
      {!terminal ? (
        <Card className="campaign-detail-card" variant="borderless">
          <h3>候选结果正在生成</h3>
          <Text type="secondary">系统正在处理当前候选池数据。</Text>
        </Card>
      ) : run.status === "FAILED" ? (
        <Alert
          type="error"
          showIcon
          title="候选结果生成失败"
          description={candidateRunFailureMessage(run.error_code)}
        />
      ) : (
        <>
          <Card
            className="candidate-summary-band campaign-detail-card"
            variant="borderless"
          >
            <Space size="large">
              <span>符合条件 {run.match_count}</span>
              <span>信息不足 {run.unknown_count}</span>
              <span>不符合条件 {run.not_match_count}</span>
            </Space>
          </Card>
          <Card
            className="candidate-results-card campaign-detail-card"
            variant="borderless"
          >
            <div className="candidate-results-toolbar">
              <Tabs
                activeKey={filter}
                onChange={(key) =>
                  changeFilter(key as "all" | "MATCH" | "UNKNOWN")
                }
                items={[
                  { key: "all", label: "全部" },
                  { key: "MATCH", label: "符合条件" },
                  { key: "UNKNOWN", label: "信息不足" },
                ]}
              />
              <div className="candidate-results-toolbar-controls">
                {canWrite ? (
                  <div className="candidate-bulk-actions">
                    <Text type="secondary">{selectedLabel}</Text>
                    <Space size="small" wrap>
                      <Button
                        size="small"
                        disabled={!pageSelection.selectedCount}
                        onClick={deselectCurrentPage}
                      >
                        取消本页
                      </Button>
                      <Button
                        size="small"
                        disabled={!selectedCount}
                        onClick={() =>
                          dispatchSelection(
                            candidateSelectionActions.clearAll(selection.scope),
                          )
                        }
                      >
                        清空
                      </Button>
                      {run.match_count > 0 && selection.mode !== "ALL_MATCH" ? (
                        <Button
                          size="small"
                          onClick={() =>
                            dispatchSelection(
                              candidateSelectionActions.selectAllMatch(
                                run.match_count,
                              ),
                            )
                          }
                        >
                          全选 {run.match_count} 人
                        </Button>
                      ) : null}
                      <Button
                        type="primary"
                        size="small"
                        disabled={!selectionPayload}
                        onClick={openCampaignSelector}
                      >
                        加入拓客活动
                      </Button>
                    </Space>
                  </div>
                ) : null}
                <span className="candidate-page-size-control">
                  <Text id="candidate-member-page-size-label" type="secondary">
                    每页
                  </Text>
                  <Select<CandidateMemberPageSize>
                    aria-label="每页数量"
                    options={CANDIDATE_MEMBER_PAGE_SIZES.map((value) => ({
                      value,
                      label: String(value),
                    }))}
                    size="small"
                    value={pageSize}
                    onChange={(value) => {
                      setPageSize(value);
                      updateMemberPageNavigation(
                        {
                          candidatePoolId: poolId,
                          filter,
                          pageSize: value,
                          runId,
                        },
                        0,
                      );
                    }}
                  />
                </span>
              </div>
            </div>
            {membersQuery.isPending ? (
              <Skeleton active paragraph={{ rows: 7 }} />
            ) : membersQuery.isError ? (
              <Alert
                type="error"
                showIcon
                title="候选结果加载失败"
                description="请稍后重试。"
                action={
                  <Button onClick={() => void membersQuery.refetch()}>
                    重新加载
                  </Button>
                }
              />
            ) : members.length === 0 ? (
              <div className="campaign-empty-state">
                <AppEmpty description={emptyLabel} />
                {filter === "all" ? (
                  <Text type="secondary">
                    本次生成没有发现符合条件或信息不足的达人。
                  </Text>
                ) : null}
              </div>
            ) : (
              <>
                <div className="campaign-table-shell">
                  <Table<CandidateMember>
                    className="candidate-pool-table candidate-results-table campaign-table"
                    rowKey="id"
                    columns={columns}
                    dataSource={members}
                    pagination={false}
                  />
                </div>
                <div className="campaign-pagination-footer">
                  <Space>
                    <Button
                      disabled={visiblePageIndex === 0}
                      onClick={previousMemberPage}
                    >
                      上一页
                    </Button>
                    <Text type="secondary">第 {visiblePageIndex + 1} 页</Text>
                    <Button
                      disabled={
                        previewMode ||
                        (!membersQuery.hasNextPage &&
                          visiblePageIndex >= memberPages.length - 1)
                      }
                      loading={
                        previewMode ? false : membersQuery.isFetchingNextPage
                      }
                      onClick={() => void nextMemberPage()}
                    >
                      下一页
                    </Button>
                  </Space>
                </div>
              </>
            )}
          </Card>
        </>
      )}
      <Drawer
        title="筛选规则"
        open={ruleOpen}
        size={640}
        onClose={() => setRuleOpen(false)}
      >
        {policy ? <PolicyDefinition definition={policy.definition} /> : null}
      </Drawer>
      <Modal
        title="调整规则并重新运行"
        open={adjusting}
        footer={null}
        width={680}
        onCancel={() => !rerunMutation.isPending && setAdjusting(false)}
      >
        {poolQuery.isPending ? (
          <Skeleton active paragraph={{ rows: 5 }} />
        ) : !currentSellerPolicy ? (
          <Alert
            type="warning"
            showIcon
            title="历史规则不可直接调整"
            description="请在候选池的当前规则上调整后重新运行。"
          />
        ) : policy && isAuthorableSellerTargetingPolicy(policy.definition) ? (
          <SellerRuleBuilder
            key={policy.id}
            initialPolicy={policy.definition}
            submitLabel="保存新规则版本并重新运行"
            loading={rerunMutation.isPending}
            error={adjustError}
            onSubmit={adjustAndRerun}
          />
        ) : null}
      </Modal>
      <Drawer
        title="候选判断依据"
        open={Boolean(evidence)}
        size={640}
        onClose={() => setEvidence(null)}
      >
        {evidence ? (
          <>
            <p>
              达人：
              <Identity member={evidence} />
            </p>
            <p>
              平台账号：
              <Account member={evidence} />
            </p>
            <p>
              结果：{" "}
              <StatusBadge
                tone={candidateResultPresentation(evidence.result).tone}
              >
                {candidateResultPresentation(evidence.result).label}
              </StatusBadge>
            </p>
            <p>
              判断原因：
              {evidence.reason_codes.map(candidateReasonLabel).join("、")}
            </p>
            {policy &&
            policyType(policy.definition) === "BUYER_V1" &&
            evidence.reason_codes.includes("CATEGORY_MISMATCH") ? (
              <Alert
                type="info"
                showIcon
                title="该候选池用于查找分类方向可能发生变化的账号，因此分类不匹配属于当前规则的命中条件。"
              />
            ) : null}
            <h3>判断依据</h3>
            <EvidenceContent member={evidence} policy={policy} />
          </>
        ) : null}
      </Drawer>
      {selectionPayload && scopedModalOpen ? (
        <CampaignSelector
          open
          selection={selectionPayload}
          selectedLabel={selectedLabel}
          unknownCount={unknownCount}
          onClose={() => setModalOpen(false)}
          onSuccess={() =>
            finishCampaignSelection(selection.scope, modalScope.generation)
          }
          onInvalidSelection={() =>
            finishCampaignSelection(selection.scope, modalScope.generation)
          }
          previewMode={previewMode}
        />
      ) : null}
    </section>
  );
}
