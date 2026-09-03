"use client";

import { ReloadOutlined, PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Modal,
  Popconfirm,
  Skeleton,
  Space,
  Table,
  Tooltip,
  Typography,
  message,
} from "antd";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { AppEmpty } from "@/components/ui/app-empty";
import { PageHeader } from "@/components/ui/page-header";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  candidateDateTime,
  candidateRunStatus,
} from "@/features/candidate-pools/formatters";
import type { CandidatePoolRun } from "@/features/candidate-pools/types";
import { ApiClientError } from "@/lib/api/client";

import {
  BuyerProspectRuleForm,
  type BuyerProspectRuleSubmitIntent,
} from "./buyer-prospect-rule-form";
import {
  useBuyerProspectRuleLifecycleMutation,
  useBuyerProspectRuleList,
  useBuyerProspectRuleOptions,
  useCreateBuyerProspectRuleMutation,
  useRunBuyerProspectRuleMutation,
  useUpdateBuyerProspectRuleMutation,
} from "./queries";
import type {
  BuyerProspectOwnerFilter,
  BuyerProspectRole,
  BuyerProspectRule,
  BuyerProspectRuleCreateRequest,
  BuyerProspectRuleStatus,
} from "./types";

const { Text } = Typography;

const tierLabels: Record<string, string> = {
  HIGH: "强潜客",
  CHANGED: "变化潜客",
  RELATED: "相关潜客",
  SAME_CATEGORY: "同类潜客",
  UNKNOWN: "待判断",
};

const recentCollectionWindowLabels: Record<string, string> = {
  "7": "近 7 天",
  "30": "近 30 天",
  "60": "近 60 天",
  "90": "近 90 天",
  ALL: "全部历史",
};

const ruleStatusPresentation: Record<
  BuyerProspectRuleStatus,
  { label: string; tone: "default" | "success" | "warning" }
> = {
  ACTIVE: { label: "已启用", tone: "success" },
  DISABLED: { label: "已停用", tone: "warning" },
  ARCHIVED: { label: "已归档", tone: "default" },
};

function idempotencyKey() {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

function mutationErrorMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiClientError)) return fallback;
  const messages: Record<string, string> = {
    AUTH_REQUIRED: "登录状态已失效，请重新登录。",
    CSRF_FAILED: "安全校验失败，请刷新页面后重试。",
    OPERATOR_REQUIRED: "请先选择当前操作人。",
    PERMISSION_DENIED: "当前账号没有执行此操作的权限。",
    VERSION_CONFLICT: "规则已被其他人更新，请刷新后重试。",
    BUYER_PROSPECT_RULE_INACTIVE: "当前规则未启用，无法运行。",
    BUYER_PROSPECT_RULE_ARCHIVED: "当前规则已归档，无法继续操作。",
  };
  return messages[error.code ?? ""] ?? fallback;
}

function followerRangeLabel(rule: BuyerProspectRule) {
  if (rule.follower_min != null && rule.follower_max != null) {
    return `${rule.follower_min} - ${rule.follower_max}`;
  }
  if (rule.follower_min != null) return `不少于 ${rule.follower_min}`;
  if (rule.follower_max != null) return `不超过 ${rule.follower_max}`;
  return "不限";
}

function ownerFilterLabel(
  filter: BuyerProspectOwnerFilter,
  operatorId: string | null,
  operators: Map<string, string>,
) {
  if (filter === "UNASSIGNED") return "未分配负责人";
  if (filter === "OPERATOR") {
    return operatorId
      ? `指定负责人：${operators.get(operatorId) ?? operatorId}`
      : "指定负责人";
  }
  return "不限负责人";
}

function latestRunSummary(run: CandidatePoolRun | null) {
  if (!run) return "暂无运行记录";
  const status = candidateRunStatus(run.status);
  if (run.status !== "COMPLETED") return status.label;
  return `${status.label} · 符合 ${run.match_count} · 信息不足 ${run.unknown_count} · 不符合 ${run.not_match_count}`;
}

function lifecycleActionLabel(status: BuyerProspectRuleStatus) {
  return status === "ACTIVE" ? "停用" : "启用";
}

export function BuyerProspectWorkspace({
  role,
  hasSelectedOperator,
}: {
  role: BuyerProspectRole;
  hasSelectedOperator: boolean;
}) {
  const router = useRouter();
  const [messageApi, contextHolder] = message.useMessage();
  const [includeArchived, setIncludeArchived] = useState(false);
  const [editingRule, setEditingRule] = useState<
    BuyerProspectRule | "new" | null
  >(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const listQuery = useBuyerProspectRuleList(includeArchived);
  const optionsQuery = useBuyerProspectRuleOptions();
  const createMutation = useCreateBuyerProspectRuleMutation();
  const updateMutation = useUpdateBuyerProspectRuleMutation();
  const runMutation = useRunBuyerProspectRuleMutation();
  const lifecycleMutation = useBuyerProspectRuleLifecycleMutation();
  const rules = useMemo(
    () => listQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [listQuery.data],
  );
  const sourceNames = useMemo(
    () =>
      new Map(
        (optionsQuery.data?.sources ?? []).map((source) => [
          source.value,
          source.label,
        ]),
      ),
    [optionsQuery.data],
  );
  const operatorNames = useMemo(
    () =>
      new Map(
        (optionsQuery.data?.operators ?? []).map((operator) => [
          operator.id,
          operator.name,
        ]),
      ),
    [optionsQuery.data],
  );
  const taxonomyLabels = useMemo(
    () =>
      new Map(
        (optionsQuery.data?.taxonomy_categories ?? []).map((category) => [
          category.id,
          category.label,
        ]),
      ),
    [optionsQuery.data],
  );
  const canWrite = role !== "viewer";
  const canMutate = canWrite && hasSelectedOperator;
  const formOpen = editingRule !== null;

  function openNewRule() {
    setFormError(null);
    setEditingRule("new");
  }

  function closeForm() {
    if (submitting) return;
    setEditingRule(null);
    setFormError(null);
  }

  async function saveRule(
    values: BuyerProspectRuleCreateRequest,
    intent: BuyerProspectRuleSubmitIntent,
  ) {
    if (!canMutate || editingRule === null) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const isNew = editingRule === "new";
      const rule = isNew
        ? await createMutation.mutateAsync({
            payload: values,
            idempotencyKey: idempotencyKey(),
          })
        : await updateMutation.mutateAsync({
            ruleId: editingRule.id,
            payload: {
              ...values,
              owner_operator_id:
                values.owner_operator_id || editingRule.owner_operator_id,
              expected_pool_version: editingRule.version,
            },
          });
      if (intent === "save_and_run") {
        await runMutation.mutateAsync({
          ruleId: rule.id,
          idempotencyKey: idempotencyKey(),
        });
      }
      setEditingRule(null);
      void messageApi.success(
        intent === "save_and_run"
          ? "筛选规则已保存并开始运行"
          : "筛选规则已保存",
      );
    } catch (caught) {
      setFormError(
        mutationErrorMessage(
          caught,
          intent === "save_and_run"
            ? "保存并运行筛选规则失败，请稍后重试。"
            : "保存筛选规则失败，请稍后重试。",
        ),
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function updateLifecycle(
    rule: BuyerProspectRule,
    status: BuyerProspectRuleStatus,
  ) {
    if (!canMutate) return;
    try {
      await lifecycleMutation.mutateAsync({
        ruleId: rule.id,
        payload: { expected_pool_version: rule.version, status },
      });
      void messageApi.success(
        status === "ARCHIVED"
          ? "筛选规则已归档"
          : status === "ACTIVE"
            ? "筛选规则已启用"
            : "筛选规则已停用",
      );
    } catch (caught) {
      void messageApi.error(
        mutationErrorMessage(caught, "更新筛选规则状态失败，请稍后重试。"),
      );
    }
  }

  const createAction = canWrite ? (
    canMutate ? (
      <Button
        type="primary"
        icon={<PlusOutlined aria-hidden="true" />}
        onClick={openNewRule}
      >
        新建筛选规则
      </Button>
    ) : (
      <Tooltip title="请先选择当前操作人">
        <Button
          type="primary"
          icon={<PlusOutlined aria-hidden="true" />}
          disabled
        >
          新建筛选规则
        </Button>
      </Tooltip>
    )
  ) : null;

  const columns = [
    {
      title: "规则名称",
      key: "name",
      width: 180,
      render: (_: unknown, rule: BuyerProspectRule) =>
        canWrite ? (
          <Button
            type="link"
            onClick={() => {
              setFormError(null);
              setEditingRule(rule);
            }}
          >
            {rule.name}
          </Button>
        ) : (
          rule.name
        ),
    },
    {
      title: "筛选条件",
      key: "conditions",
      render: (_: unknown, rule: BuyerProspectRule) => (
        <Space direction="vertical" size={2}>
          <span>
            当前达人类目：
            {rule.category_ids
              .map(
                (categoryId) => taxonomyLabels.get(categoryId) ?? "未识别类目",
              )
              .join("、") || "不限"}
          </span>
          <span>粉丝数：{followerRangeLabel(rule)}</span>
          <span>
            潜客等级：
            {rule.buyer_lead_tiers
              .map((tier) => tierLabels[tier] ?? tier)
              .join("、") || "—"}
          </span>
          <span>
            数据来源：{sourceNames.get(rule.source_type) ?? rule.source_label}
          </span>
          <span>
            最近采集：
            {recentCollectionWindowLabels[rule.recent_collection_window] ??
              rule.recent_collection_window}
          </span>
          <span>
            负责人筛选：
            {ownerFilterLabel(
              rule.prospect_owner_filter,
              rule.prospect_owner_operator_id,
              operatorNames,
            )}
          </span>
          <span>{rule.exclude_contacted ? "排除已触达" : "不排除已触达"}</span>
        </Space>
      ),
    },
    {
      title: "状态",
      key: "status",
      width: 100,
      render: (_: unknown, rule: BuyerProspectRule) => {
        const status = ruleStatusPresentation[rule.status];
        return <StatusBadge tone={status.tone}>{status.label}</StatusBadge>;
      },
    },
    {
      title: "最近运行 / 结果",
      key: "latest_run",
      width: 220,
      render: (_: unknown, rule: BuyerProspectRule) => (
        <Space direction="vertical" size={2}>
          <span>{latestRunSummary(rule.latest_run)}</span>
          {rule.latest_run ? (
            <Text type="secondary">
              {candidateDateTime(rule.latest_run.updated_at)}
            </Text>
          ) : null}
        </Space>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 250,
      render: (_: unknown, rule: BuyerProspectRule) => (
        <Space size={[0, 4]} wrap>
          <Button
            type="link"
            disabled={!rule.latest_run}
            onClick={() => {
              if (!rule.latest_run) return;
              router.push(
                `/candidate-pools/${encodeURIComponent(rule.latest_run.pool_id)}/runs/${encodeURIComponent(rule.latest_run.id)}`,
              );
            }}
          >
            查看客户
          </Button>
          {canMutate && rule.status !== "ARCHIVED" ? (
            <Button
              type="link"
              loading={lifecycleMutation.isPending}
              onClick={() =>
                void updateLifecycle(
                  rule,
                  rule.status === "ACTIVE" ? "DISABLED" : "ACTIVE",
                )
              }
            >
              {lifecycleActionLabel(rule.status)}
            </Button>
          ) : null}
          {canMutate && rule.status !== "ARCHIVED" ? (
            <Popconfirm
              title="归档筛选规则？"
              description="归档后规则不会出现在默认列表中，且不能继续运行。"
              okText="归档"
              cancelText="取消"
              onConfirm={() => void updateLifecycle(rule, "ARCHIVED")}
            >
              <Button type="link" danger loading={lifecycleMutation.isPending}>
                归档
              </Button>
            </Popconfirm>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <section className="campaign-workspace" aria-label="潜在客户">
      {contextHolder}
      <PageHeader
        title="潜在客户"
        description="保存筛选规则，生成并查看潜在客户名单。"
        extra={
          <Space wrap>
            <Checkbox
              checked={includeArchived}
              onChange={(event) => setIncludeArchived(event.target.checked)}
            >
              显示已归档
            </Checkbox>
            <Button
              icon={<ReloadOutlined aria-hidden="true" />}
              loading={listQuery.isFetching && !listQuery.isPending}
              onClick={() => void listQuery.refetch()}
            >
              刷新
            </Button>
            {createAction}
          </Space>
        }
      />
      <Alert
        type="info"
        showIcon
        message="潜客等级仅用于销售线索排序，不代表已发生账号购买或交易。"
      />
      <Card className="candidate-pool-list-card" variant="borderless">
        {listQuery.isPending ? (
          <Skeleton active paragraph={{ rows: 7 }} />
        ) : listQuery.isError ? (
          <Alert
            type="error"
            showIcon
            title="潜在客户规则加载失败"
            description="请稍后重试。"
            action={
              <Button onClick={() => void listQuery.refetch()}>重新加载</Button>
            }
          />
        ) : rules.length === 0 ? (
          <div className="campaign-empty-state">
            <AppEmpty description="暂无筛选规则" />
            <Text type="secondary">创建筛选规则后即可生成潜在客户名单。</Text>
            {canMutate ? (
              <Button type="primary" onClick={openNewRule}>
                新建筛选规则
              </Button>
            ) : null}
          </div>
        ) : (
          <>
            <div className="campaign-table-shell">
              <Table<BuyerProspectRule>
                className="candidate-pool-table campaign-table"
                rowKey="id"
                columns={columns}
                dataSource={rules}
                pagination={false}
              />
            </div>
            <div className="campaign-pagination-footer">
              {listQuery.hasNextPage ? (
                <Button
                  icon={<ReloadOutlined aria-hidden="true" />}
                  loading={listQuery.isFetchingNextPage}
                  onClick={() => void listQuery.fetchNextPage()}
                >
                  加载更多
                </Button>
              ) : (
                <Text type="secondary">已经到底了</Text>
              )}
            </div>
          </>
        )}
      </Card>
      <Modal
        title={editingRule === "new" ? "新建筛选规则" : "编辑筛选规则"}
        open={formOpen}
        footer={null}
        width={680}
        destroyOnHidden
        onCancel={closeForm}
      >
        {optionsQuery.isPending ? (
          <Skeleton active paragraph={{ rows: 8 }} />
        ) : optionsQuery.isError || !optionsQuery.data ? (
          <Alert
            type="error"
            showIcon
            title="筛选规则选项加载失败"
            description="请刷新后重试。"
            action={
              <Button onClick={() => void optionsQuery.refetch()}>
                重新加载
              </Button>
            }
          />
        ) : editingRule ? (
          <BuyerProspectRuleForm
            key={editingRule === "new" ? "new" : editingRule.id}
            options={optionsQuery.data}
            rule={editingRule === "new" ? undefined : editingRule}
            submitLabel="保存"
            saveAndRunLabel="保存并运行"
            loading={submitting}
            error={formError}
            onSubmit={(values, intent) => void saveRule(values, intent)}
          />
        ) : null}
      </Modal>
    </section>
  );
}
