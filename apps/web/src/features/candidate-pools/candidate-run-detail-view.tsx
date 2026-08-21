"use client";

import { ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Drawer,
  Modal,
  Radio,
  Skeleton,
  Space,
  Table,
  Tabs,
  Typography,
  message,
} from "antd";
import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AppEmpty } from "@/components/ui/app-empty";
import { StatusBadge } from "@/components/ui/status-badge";
import { fetchCampaignPage } from "@/features/campaigns/api";
import { campaignStatusPresentation } from "@/features/campaigns/formatters";
import type { Campaign } from "@/features/campaigns/types";
import { platformLabel } from "@/features/influencers/formatters";

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
import {
  useAddCandidatesToCampaignMutation,
  useCandidateMembers,
  useCandidatePolicies,
  useCandidateRun,
} from "./queries";
import type {
  CandidateCampaignAddResult,
  CandidateMember,
  CandidatePoolRole,
  TargetingPolicy,
} from "./types";

const { Text } = Typography;
const attemptKey = () =>
  globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

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
    <span>
      {platformLabel(account.platform)} · {account.account_name}
      {account.account_handle ? (
        <Text type="secondary"> · @{account.account_handle}</Text>
      ) : null}
      {!account.is_active ? <Text type="secondary"> · 账号已停用</Text> : null}
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
            render: (value: unknown) => {
              const item =
                value && typeof value === "object" && !Array.isArray(value)
                  ? (value as Record<string, unknown>)
                  : {};
              return safeList(item.allowed ?? item.minimum ?? item.maximum);
            },
          },
          {
            title: "观察值",
            dataIndex: "observed",
            render: (value: unknown) => {
              const item =
                value && typeof value === "object" && !Array.isArray(value)
                  ? (value as Record<string, unknown>)
                  : {};
              return safeString(item.value ?? item.status) ?? "—";
            },
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
  selected,
  runId,
  onClose,
  onSuccess,
  onInvalidSelection,
  previewMode = false,
}: {
  open: boolean;
  selected: CandidateMember[];
  runId: string;
  onClose: () => void;
  onSuccess: () => void;
  onInvalidSelection: () => void;
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
  const unknownCount = selected.filter(
    (member) => member.result === "UNKNOWN",
  ).length;
  async function submit() {
    if (!campaignId) return;
    if (previewMode) {
      onSuccess();
      return;
    }
    const selectedCampaignId = campaignId;
    const memberIds = selected.map((member) => member.id);
    const payload = JSON.stringify({ campaignId, memberIds });
    const current =
      retry?.payload === payload ? retry : { key: attemptKey(), payload };
    setError(null);
    try {
      const result = await mutation.mutateAsync({
        campaignId,
        runId,
        memberIds,
        idempotencyKey: current.key,
      });
      void messageApi.success(
        <CandidateCampaignSuccessFeedback
          campaignId={selectedCampaignId}
          result={result}
        />,
      );
      setRetry(null);
      onSuccess();
    } catch (caught) {
      const label = mutationErrorMessage(
        caught,
        "加入拓客活动失败，请稍后重试。",
      );
      setError(label);
      if (isAmbiguousMutation(caught)) setRetry(current);
      else if ((caught as { code?: string }).code === "CAMPAIGN_CLOSED") {
        setCampaignId(null);
        void campaigns.refetch();
      } else if (
        [
          "CANDIDATE_POOL_RUN_MEMBER_NOT_FOUND",
          "CANDIDATE_POOL_RUN_MEMBER_AMBIGUOUS",
        ].includes((caught as { code?: string }).code ?? "")
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
      <Text>已选择 {selected.length} 位候选达人</Text>
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
  const runQuery = useCandidateRun(poolId, runId, activePolling);
  const policies = useCandidatePolicies(poolId, Boolean(runQuery.data));
  const [filter, setFilter] = useState<"all" | "MATCH" | "UNKNOWN">("all");
  const membersQuery = useCandidateMembers(
    poolId,
    runId,
    filter === "all" ? undefined : filter,
    runQuery.data?.status === "COMPLETED",
  );
  const [selected, setSelected] = useState<Record<string, CandidateMember>>(
    () =>
      Object.fromEntries(
        (previewSelectedMembers ?? []).map((item) => [item.id, item]),
      ),
  );
  const [evidence, setEvidence] = useState<CandidateMember | null>(
    previewMode ? null : (previewEvidence ?? null),
  );
  const [modalOpen, setModalOpen] = useState(
    previewMode ? false : previewModalOpen,
  );
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
    const timer = window.setTimeout(() => setModalOpen(true), 0);
    return () => window.clearTimeout(timer);
  }, [previewMode, previewModalOpen]);
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
  const members = useMemo(
    () => membersQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [membersQuery.data],
  );
  const canWrite = role !== "viewer" && hasSelectedOperator;
  function changeFilter(next: "all" | "MATCH" | "UNKNOWN") {
    setFilter(next);
    setSelected({});
  }
  function toggle(member: CandidateMember, checked: boolean) {
    setSelected((current) => {
      if (!checked) {
        const copy = { ...current };
        delete copy[member.id];
        return copy;
      }
      const sameInfluencer = Object.values(current).find(
        (item) => item.influencer_id === member.influencer_id,
      );
      if (sameInfluencer && sameInfluencer.id !== member.id) {
        void messageApi.warning(
          "同一达人只能选择一个平台账号，请先取消已选账号。",
        );
        return current;
      }
      return { ...current, [member.id]: member };
    });
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
            title: "选择",
            key: "select",
            render: (_: unknown, member: CandidateMember) => (
              <input
                aria-label={`选择 ${member.influencer.display_name}`}
                type="checkbox"
                checked={Boolean(selected[member.id])}
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
          <Text type="secondary">　规则版本：版本 {policy.version}</Text>
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
              {canWrite ? (
                <Space>
                  <Text type="secondary">
                    已选择 {Object.keys(selected).length} 位候选达人
                  </Text>
                  <Button
                    type="primary"
                    disabled={!Object.keys(selected).length}
                    onClick={() => setModalOpen(true)}
                  >
                    加入拓客活动
                  </Button>
                </Space>
              ) : null}
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
                  {membersQuery.hasNextPage ? (
                    <Button
                      icon={<ReloadOutlined aria-hidden="true" />}
                      loading={
                        previewMode ? false : membersQuery.isFetchingNextPage
                      }
                      onClick={() => {
                        if (!previewMode) void membersQuery.fetchNextPage();
                      }}
                    >
                      加载更多
                    </Button>
                  ) : null}
                </div>
              </>
            )}
          </Card>
        </>
      )}
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
      <CampaignSelector
        open={modalOpen}
        selected={Object.values(selected)}
        runId={runId}
        onClose={() => setModalOpen(false)}
        onSuccess={() => {
          setModalOpen(false);
          setSelected({});
        }}
        onInvalidSelection={() => setSelected({})}
        previewMode={previewMode}
      />
    </section>
  );
}
