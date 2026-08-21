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
import type {
  CandidatePoolRole,
  CandidatePoolRun,
  TargetingPolicy,
  TargetingPolicyDefinition,
} from "./types";

const { Text } = Typography;
type CreateAttempt = { key: string };
const attemptKey = () =>
  globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

function Owner({ name, status }: { name: string; status: string }) {
  return (
    <span>
      {name}
      {status === "disabled" ? <Text type="secondary"> · 已停用</Text> : null}
    </span>
  );
}

function PolicyDefinition({
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
    const values: Array<[string, string | null]> = [
      ["粉丝数", rangeLabel(seller.followers)],
      ["近 7 天笔记数", rangeLabel(seller.notes_7d)],
      ["近 60 天笔记数", rangeLabel(seller.notes_60d)],
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
}: {
  poolId: string;
  currentPolicyId: string | null;
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
      render: (_: unknown, item: TargetingPolicy) => (
        <Button type="link" onClick={() => setPolicy(item)}>
          查看规则
        </Button>
      ),
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
}: {
  poolId: string;
  previewMode?: boolean;
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
}: {
  poolId: string;
  role: CandidatePoolRole;
  hasSelectedOperator: boolean;
  previewMode?: boolean;
  previewTab?: "basic" | "policies" | "runs";
}) {
  const query = useCandidatePool(poolId);
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState<CreateAttempt | null>(null);
  const mutation = useCreateCandidateRunMutation();
  const [messageApi, holder] = message.useMessage();
  const tab =
    previewTab ??
    (params.get("tab") === "policies"
      ? "policies"
      : params.get("tab") === "runs"
        ? "runs"
        : "basic");
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
        activeKey={previewMode ? undefined : tab}
        defaultActiveKey={previewMode ? tab : undefined}
        onChange={
          previewMode
            ? undefined
            : (key) => {
                router.push(
                  key === "basic" ? pathname : `${pathname}?tab=${key}`,
                );
              }
        }
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
                />
              ) : null,
          },
          {
            key: "runs",
            label: "生成记录",
            children:
              tab === "runs" ? (
                <RunHistory poolId={poolId} previewMode={previewMode} />
              ) : null,
          },
        ]}
      />
      {showCreate ? (
        <Button type="primary" onClick={() => setOpen(true)}>
          生成候选结果
        </Button>
      ) : null}
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
    </section>
  );
}
