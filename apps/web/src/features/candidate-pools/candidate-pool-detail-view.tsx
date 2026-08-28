"use client";

import { ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Modal,
  Skeleton,
  Space,
  Table,
  Tabs,
  Typography,
  message,
} from "antd";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { AppEmpty } from "@/components/ui/app-empty";
import { StatusBadge } from "@/components/ui/status-badge";
import { ApiClientError } from "@/lib/api/client";

import {
  candidateDateTime,
  candidatePoolKindLabel,
  candidatePoolStatus,
  candidateRunStatus,
  isAmbiguousMutation,
  mutationErrorMessage,
  policyType,
  rangeLabel,
} from "./formatters";
import {
  useCandidatePolicies,
  useCandidatePool,
  useCandidateRuns,
  useCreateCandidateRunMutation,
} from "./queries";
import { SellerRuleBuilder } from "./seller-rule-builder";
import { isAuthorableSellerTargetingPolicy } from "./types";
import type {
  CandidatePoolRole,
  CandidatePoolRun,
  SellerTargetingPolicy,
  TargetingPolicy,
  TargetingPolicyDefinition,
} from "./types";

const { Text } = Typography;
type CreateAttempt = { key: string };
export const CANDIDATE_POOL_DETAIL_TABS = [
  "basic",
  "policies",
  "runs",
] as const;
export type CandidatePoolDetailTab =
  (typeof CANDIDATE_POOL_DETAIL_TABS)[number];
const attemptKey = () =>
  globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

function candidatePoolDetailTab(value: string | null): CandidatePoolDetailTab {
  return value === "policies" || value === "runs" ? value : "basic";
}

function Owner({ name, status }: { name: string; status: string }) {
  return (
    <span>
      {name}
      {status === "disabled" ? <Text type="secondary"> · 已停用</Text> : null}
    </span>
  );
}

export function PolicyDefinition({
  definition,
}: {
  definition: TargetingPolicyDefinition;
}) {
  if (definition.schema_version !== 1 || !policyType(definition))
    return <Text type="secondary">该规则版本暂不支持完整展示。</Text>;
  if (definition.policy_type === "SELLER_V1") {
    const seller = definition as Extract<
      TargetingPolicyDefinition,
      { policy_type: "SELLER_V1" }
    >;
    const contentActivity =
      "content_activity" in seller ? seller.content_activity : null;
    const longInactivity =
      "long_inactivity" in seller ? seller.long_inactivity : null;
    const values: Array<[string, string | null]> = [
      [
        "联系方式",
        seller.contact_availability === "has_contact"
          ? "有联系方式"
          : seller.contact_availability === "has_email"
            ? "有邮箱"
            : seller.contact_availability === "no_contact"
              ? "无联系方式"
              : (seller.contact_availability?.types?.join("、") ?? null),
      ],
      ["粉丝数", rangeLabel(seller.followers)],
      ["近 7 天笔记数", rangeLabel(seller.notes_7d)],
      ["近 60 天笔记数", rangeLabel(seller.notes_60d)],
      [
        "内容活跃度",
        contentActivity?.minimum_inactive_days != null
          ? `断更不少于 ${contentActivity.minimum_inactive_days} 天`
          : null,
      ],
      [
        "长期断更",
        longInactivity?.minimum_inactive_days != null
          ? `断更不少于 ${longInactivity.minimum_inactive_days} 天`
          : null,
      ],
      ["标签", seller.tags_exact_any?.join("、") ?? null],
      ["平台", seller.platforms?.join("、") ?? null],
      ["数据来源", seller.sources?.join("、") ?? null],
      ["允许的新鲜度", seller.freshness?.allowed_statuses?.join("、") ?? null],
    ];
    return (
      <Descriptions column={1} size="small">
        {values
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <Descriptions.Item key={label} label={label}>
              {value}
            </Descriptions.Item>
          ))}
      </Descriptions>
    );
  }
  const buyer = definition as Extract<
    TargetingPolicyDefinition,
    { policy_type: "BUYER_V1" }
  >;
  const taxonomy = buyer.taxonomy;
  return (
    <Descriptions column={1} size="small">
      <Descriptions.Item label="分类版本">
        {taxonomy.taxonomy_version}
      </Descriptions.Item>
      <Descriptions.Item label="审核状态">
        {taxonomy.reviewed ? "已审核" : "未审核"}
      </Descriptions.Item>
      <Descriptions.Item label="分类">
        {taxonomy.categories?.join("、") || "—"}
      </Descriptions.Item>
      <Descriptions.Item label="别名">
        {taxonomy.aliases
          ?.map((item: { label: string }) => item.label)
          .join("；") || "—"}
      </Descriptions.Item>
      <Descriptions.Item label="允许的新鲜度">
        {buyer.freshness?.allowed_statuses?.join("、") || "—"}
      </Descriptions.Item>
    </Descriptions>
  );
}

function PolicyHistory({
  poolId,
  currentPolicyId,
  canMutate,
  onRerun,
  onAdjust,
}: {
  poolId: string;
  currentPolicyId: string | null;
  canMutate: boolean;
  onRerun: (policy: TargetingPolicy) => void;
  onAdjust: (policy: TargetingPolicy) => void;
}) {
  const query = useCandidatePolicies(poolId, true);
  const [policy, setPolicy] = useState<TargetingPolicy | null>(null);
  const policies = useMemo(
    () =>
      (query.data ?? [])
        .slice()
        .sort((left, right) => left.version - right.version),
    [query.data],
  );
  const columns = [
    {
      title: "版本",
      key: "version",
      render: (_: unknown, item: TargetingPolicy) => (
        <span>
          版本 {item.version}
          {item.id === currentPolicyId ? (
            <span className="candidate-current-policy">当前规则</span>
          ) : null}
        </span>
      ),
    },
    {
      title: "规则类型",
      key: "type",
      render: (_: unknown, item: TargetingPolicy) =>
        item.definition.schema_version === 1 && policyType(item.definition)
          ? item.definition.policy_type === "SELLER_V1"
            ? "卖家规则"
            : "买家规则"
          : "未知规则类型",
    },
    {
      title: "创建时间",
      key: "created_at",
      render: (_: unknown, item: TargetingPolicy) =>
        candidateDateTime(item.created_at),
    },
    {
      title: "操作",
      key: "action",
      render: (_: unknown, item: TargetingPolicy) => {
        const seller = isAuthorableSellerTargetingPolicy(item.definition);
        const buyerRerunnable =
          item.definition.schema_version === 1 &&
          item.definition.policy_type === "BUYER_V1" &&
          (item.definition as { taxonomy?: { reviewed?: unknown } }).taxonomy
            ?.reviewed === true;
        return (
          <Space size="small" wrap>
            <Button type="link" onClick={() => setPolicy(item)}>
              查看规则
            </Button>
            {canMutate && (seller || buyerRerunnable) ? (
              <Button type="link" onClick={() => onRerun(item)}>
                重新运行此规则
              </Button>
            ) : null}
            {canMutate && seller && item.id === currentPolicyId ? (
              <Button type="link" onClick={() => onAdjust(item)}>
                调整规则并重新运行
              </Button>
            ) : null}
          </Space>
        );
      },
    },
  ];
  return (
    <Card
      className="candidate-detail-card campaign-detail-card"
      variant="borderless"
    >
      {query.isPending ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          title="规则历史加载失败"
          description="请稍后重试。"
          action={
            <Button onClick={() => void query.refetch()}>重新加载</Button>
          }
        />
      ) : (
        <Table<TargetingPolicy>
          rowKey="id"
          columns={columns}
          dataSource={policies}
          pagination={false}
        />
      )}
      <Drawer
        title="筛选规则"
        open={Boolean(policy)}
        size={640}
        onClose={() => setPolicy(null)}
      >
        {policy ? (
          <>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="版本">
                版本 {policy.version}
              </Descriptions.Item>
              <Descriptions.Item label="当前规则">
                {policy.id === currentPolicyId ? "是" : "否"}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {candidateDateTime(policy.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label="规则类型">
                {policyType(policy.definition) === "SELLER_V1"
                  ? "卖家规则"
                  : policyType(policy.definition) === "BUYER_V1"
                    ? "买家规则"
                    : "未知规则类型"}
              </Descriptions.Item>
            </Descriptions>
            <PolicyDefinition definition={policy.definition} />
          </>
        ) : null}
      </Drawer>
    </Card>
  );
}

function RunHistory({
  poolId,
  previewMode = false,
  onCreate,
}: {
  poolId: string;
  previewMode?: boolean;
  onCreate?: () => void;
}) {
  const query = useCandidateRuns(poolId, true);
  const runs = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data],
  );
  const columns = [
    {
      title: "创建时间",
      key: "created",
      render: (_: unknown, run: CandidatePoolRun) =>
        candidateDateTime(run.created_at),
    },
    {
      title: "数据基准时间",
      key: "asof",
      render: (_: unknown, run: CandidatePoolRun) =>
        candidateDateTime(run.as_of),
    },
    {
      title: "状态",
      key: "status",
      render: (_: unknown, run: CandidatePoolRun) => {
        const status = candidateRunStatus(run.status);
        return <StatusBadge tone={status.tone}>{status.label}</StatusBadge>;
      },
    },
    ...(["符合条件", "信息不足", "不符合条件"] as const).map(
      (title, index) => ({
        title,
        key: title,
        render: (_: unknown, run: CandidatePoolRun) =>
          run.status === "COMPLETED"
            ? [run.match_count, run.unknown_count, run.not_match_count][index]
            : "—",
      }),
    ),
    {
      title: "操作",
      key: "action",
      render: (_: unknown, run: CandidatePoolRun) => (
        <Link
          href={`/candidate-pools/${encodeURIComponent(poolId)}/runs/${encodeURIComponent(run.id)}`}
        >
          查看详情
        </Link>
      ),
    },
  ];
  return (
    <Card
      className="candidate-detail-card campaign-detail-card"
      variant="borderless"
    >
      <div className="candidate-run-history-header">
        <Text strong>生成记录</Text>
        {onCreate ? (
          <Button type="primary" onClick={onCreate}>
            生成候选结果
          </Button>
        ) : null}
      </div>
      {query.isPending ? (
        <Skeleton active paragraph={{ rows: 7 }} />
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          title="生成记录加载失败"
          description="请稍后重试。"
          action={
            <Button onClick={() => void query.refetch()}>重新加载</Button>
          }
        />
      ) : runs.length === 0 ? (
        <div className="campaign-empty-state">
          <AppEmpty description="暂无生成记录" />
        </div>
      ) : (
        <>
          <div className="campaign-table-shell">
            <Table<CandidatePoolRun>
              className="candidate-pool-table campaign-table"
              rowKey="id"
              columns={columns}
              dataSource={runs}
              pagination={false}
            />
          </div>
          <div className="campaign-pagination-footer">
            {query.hasNextPage ? (
              <Button
                icon={<ReloadOutlined aria-hidden="true" />}
                loading={previewMode ? false : query.isFetchingNextPage}
                onClick={() => {
                  if (!previewMode) void query.fetchNextPage();
                }}
              >
                加载更多
              </Button>
            ) : null}
          </div>
        </>
      )}
    </Card>
  );
}

export function CandidatePoolDetailView({
  poolId,
  role,
  hasSelectedOperator,
  previewMode = false,
  previewTab,
  onPreviewTabChange,
}: {
  poolId: string;
  role: CandidatePoolRole;
  hasSelectedOperator: boolean;
  previewMode?: boolean;
  previewTab?: CandidatePoolDetailTab;
  onPreviewTabChange?: (tab: CandidatePoolDetailTab) => void;
}) {
  const query = useCandidatePool(poolId);
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState<CreateAttempt | null>(null);
  const [adjusting, setAdjusting] = useState<TargetingPolicy | null>(null);
  const [adjustError, setAdjustError] = useState<string | null>(null);
  const [adjustRetry, setAdjustRetry] = useState<{
    key: string;
    payload: string;
  } | null>(null);
  const mutation = useCreateCandidateRunMutation();
  const [messageApi, holder] = message.useMessage();
  const tab = previewTab ?? candidatePoolDetailTab(params.get("tab"));
  const pool = query.data;
  const canMutate = role !== "viewer" && hasSelectedOperator;
  async function start(attempt?: CreateAttempt) {
    if (!pool || !canMutate) return;
    if (previewMode) {
      setOpen(false);
      return;
    }
    const next = attempt ?? { key: attemptKey() };
    setError(null);
    try {
      const run = await mutation.mutateAsync({
        poolId,
        idempotencyKey: next.key,
      });
      setOpen(false);
      setRetry(null);
      void messageApi.success("候选结果已开始生成");
      router.push(
        `/candidate-pools/${encodeURIComponent(poolId)}/runs/${encodeURIComponent(run.id)}`,
      );
    } catch (caught) {
      if (isAmbiguousMutation(caught)) {
        setRetry(next);
        setError("生成结果暂时无法确认。你可以重试本次生成。");
      } else {
        setRetry(null);
        setError(
          mutationErrorMessage(caught, "生成候选结果失败，请稍后重试。"),
        );
      }
    }
  }
  async function rerunPolicy(policy: TargetingPolicy) {
    if (!pool || !showCreate || previewMode) return;
    try {
      const run = await mutation.mutateAsync({
        poolId,
        idempotencyKey: attemptKey(),
        payload: { policy_id: policy.id },
      });
      void messageApi.success("候选结果已开始生成");
      router.push(
        `/candidate-pools/${encodeURIComponent(poolId)}/runs/${encodeURIComponent(run.id)}`,
      );
    } catch (caught) {
      void messageApi.error(
        mutationErrorMessage(caught, "重新运行规则失败，请稍后重试。"),
      );
    }
  }
  async function adjustAndRerun(policy: SellerTargetingPolicy) {
    if (!pool || !adjusting || previewMode) return;
    const request = {
      base_policy_id: adjusting.id,
      expected_pool_version: pool.version,
      policy,
    };
    const serialized = JSON.stringify(request);
    const attempt =
      adjustRetry?.payload === serialized
        ? adjustRetry
        : { key: attemptKey(), payload: serialized };
    setAdjustError(null);
    try {
      const run = await mutation.mutateAsync({
        poolId,
        idempotencyKey: attempt.key,
        payload: request,
      });
      setAdjustRetry(null);
      setAdjusting(null);
      void messageApi.success("新规则版本已保存，候选结果已开始生成");
      router.push(
        `/candidate-pools/${encodeURIComponent(poolId)}/runs/${encodeURIComponent(run.id)}`,
      );
    } catch (caught) {
      if (
        caught instanceof ApiClientError &&
        caught.code === "VERSION_CONFLICT"
      ) {
        setAdjustRetry(null);
        setAdjustError("规则已被其他人更新。请刷新后在当前规则上重新调整。");
        void query.refetch();
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
  if (query.isPending)
    return (
      <section className="campaign-workspace">
        <Skeleton active paragraph={{ rows: 7 }} />
      </section>
    );
  if (query.isError || !pool)
    return (
      <section className="campaign-workspace">
        <Alert
          type="error"
          showIcon
          title="候选池加载失败"
          description="请稍后重试。"
          action={
            <Button onClick={() => void query.refetch()}>重新加载</Button>
          }
        />
      </section>
    );
  const status = candidatePoolStatus(pool.status);
  const currentPolicyUsable = Boolean(pool.current_policy_id);
  const showCreate =
    canMutate && pool.status === "ACTIVE" && currentPolicyUsable;
  return (
    <section
      className="candidate-pool-detail-workspace campaign-workspace"
      aria-label="候选池详情"
    >
      {holder}
      <div>
        <h2 className="page-title">
          {pool.name}{" "}
          <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
        </h2>
        <Text>
          负责人：
          <Owner name={pool.owner.name} status={pool.owner.status} />
        </Text>
        <Text type="secondary">
          　更新于：{candidateDateTime(pool.updated_at)}
        </Text>
      </div>
      {pool.status !== "ACTIVE" ? (
        <Text type="secondary">当前候选池已归档，无法生成新的候选结果。</Text>
      ) : !currentPolicyUsable ? (
        <Text type="secondary">
          当前候选池没有可用规则，暂时无法生成候选结果。
        </Text>
      ) : null}
      <Tabs
        activeKey={tab}
        onChange={(key) => {
          const nextTab = candidatePoolDetailTab(key);
          if (nextTab === tab) return;
          if (previewMode) {
            onPreviewTabChange?.(nextTab);
            return;
          }
          router.push(
            nextTab === "basic" ? pathname : `${pathname}?tab=${nextTab}`,
          );
        }}
        items={[
          {
            key: "basic",
            label: "基本信息",
            children: (
              <Card
                className="candidate-detail-card campaign-detail-card"
                variant="borderless"
              >
                <Descriptions column={1}>
                  <Descriptions.Item label="候选池名称">
                    {pool.name}
                  </Descriptions.Item>
                  <Descriptions.Item label="类型">
                    {candidatePoolKindLabel(pool.kind)}
                  </Descriptions.Item>
                  <Descriptions.Item label="状态">
                    <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                  </Descriptions.Item>
                  <Descriptions.Item label="负责人">
                    <Owner name={pool.owner.name} status={pool.owner.status} />
                  </Descriptions.Item>
                  <Descriptions.Item label="创建时间">
                    {candidateDateTime(pool.created_at)}
                  </Descriptions.Item>
                  <Descriptions.Item label="更新时间">
                    {candidateDateTime(pool.updated_at)}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ),
          },
          {
            key: "policies",
            label: "规则历史",
            children:
              tab === "policies" ? (
                <PolicyHistory
                  poolId={poolId}
                  currentPolicyId={pool.current_policy_id}
                  canMutate={showCreate}
                  onRerun={(policy) => void rerunPolicy(policy)}
                  onAdjust={(policy) => {
                    if (!isAuthorableSellerTargetingPolicy(policy.definition))
                      return;
                    setAdjustError(null);
                    setAdjustRetry(null);
                    setAdjusting(policy);
                  }}
                />
              ) : null,
          },
          {
            key: "runs",
            label: "生成记录",
            children:
              tab === "runs" ? (
                <RunHistory
                  poolId={poolId}
                  previewMode={previewMode}
                  onCreate={showCreate ? () => setOpen(true) : undefined}
                />
              ) : null,
          },
        ]}
      />
      <Modal
        title="生成新的候选结果？"
        open={open}
        okText="开始生成"
        cancelText="取消"
        confirmLoading={mutation.isPending}
        onCancel={() => !mutation.isPending && setOpen(false)}
        onOk={() => void start(retry ?? undefined)}
      >
        {error ? (
          <Alert
            type="error"
            showIcon
            title={error}
            className="campaign-modal-alert"
          />
        ) : null}
        <p>系统将使用当前规则和当前数据生成一份新的候选结果。</p>
      </Modal>
      <Modal
        title="调整规则并重新运行"
        open={Boolean(adjusting)}
        footer={null}
        width={680}
        onCancel={() => !mutation.isPending && setAdjusting(null)}
      >
        {adjusting &&
        isAuthorableSellerTargetingPolicy(adjusting.definition) ? (
          <SellerRuleBuilder
            key={adjusting.id}
            initialPolicy={adjusting.definition}
            submitLabel="保存新规则版本并重新运行"
            loading={mutation.isPending}
            error={adjustError}
            onSubmit={adjustAndRerun}
          />
        ) : null}
      </Modal>
    </section>
  );
}
