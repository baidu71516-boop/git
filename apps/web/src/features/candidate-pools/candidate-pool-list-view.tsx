"use client";

import { ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Skeleton,
  Table,
  Typography,
  message,
} from "antd";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { AppEmpty } from "@/components/ui/app-empty";
import { StatusBadge } from "@/components/ui/status-badge";

import {
  candidateDateTime,
  candidatePoolKindLabel,
  candidatePoolStatus,
} from "./formatters";
import { isAmbiguousMutation, mutationErrorMessage } from "./formatters";
import {
  useCandidatePoolList,
  useCreateCandidatePoolMutation,
} from "./queries";
import { SellerRuleBuilder } from "./seller-rule-builder";
import type {
  CandidatePool,
  CandidatePoolRole,
  SellerTargetingPolicy,
} from "./types";

const { Text } = Typography;

function Owner({ pool }: { pool: CandidatePool }) {
  return (
    <span>
      {pool.owner.name}
      {pool.owner.status === "disabled" ? (
        <Text type="secondary"> · 已停用</Text>
      ) : null}
    </span>
  );
}

export function CandidatePoolListView({
  previewState,
  previewMode = false,
  role = "viewer",
  hasSelectedOperator = false,
}: {
  previewState?: "empty" | "error";
  previewMode?: boolean;
  role?: CandidatePoolRole;
  hasSelectedOperator?: boolean;
}) {
  const query = useCandidatePoolList();
  const router = useRouter();
  const mutation = useCreateCandidatePoolMutation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState<{ key: string; payload: string } | null>(
    null,
  );
  const [messageApi, holder] = message.useMessage();
  const pools = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data],
  );
  const canCreate = role !== "viewer" && hasSelectedOperator;
  async function create(policy: SellerTargetingPolicy) {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("请输入候选池名称。");
      return;
    }
    if (previewMode) {
      setOpen(false);
      return;
    }
    const payload = {
      name: trimmedName,
      kind: "POTENTIAL_SELLER" as const,
      policy,
    };
    const serialized = JSON.stringify(payload);
    const attempt =
      retry?.payload === serialized
        ? retry
        : {
            key:
              globalThis.crypto?.randomUUID?.() ??
              `${Date.now()}-${Math.random()}`,
            payload: serialized,
          };
    setError(null);
    try {
      const pool = await mutation.mutateAsync({
        payload,
        idempotencyKey: attempt.key,
      });
      setRetry(null);
      setOpen(false);
      void messageApi.success("候选池已创建");
      router.push(`/candidate-pools/${encodeURIComponent(pool.id)}`);
    } catch (caught) {
      if (isAmbiguousMutation(caught)) {
        setRetry(attempt);
        setError("创建结果暂时无法确认。你可以重试本次创建。");
      } else {
        setRetry(null);
        setError(mutationErrorMessage(caught, "创建候选池失败，请稍后重试。"));
      }
    }
  }
  const columns = [
    { title: "候选池名称", dataIndex: "name", key: "name", width: 250 },
    {
      title: "类型",
      key: "kind",
      width: 128,
      render: (_: unknown, pool: CandidatePool) =>
        candidatePoolKindLabel(pool.kind),
    },
    {
      title: "状态",
      key: "status",
      width: 110,
      render: (_: unknown, pool: CandidatePool) => {
        const status = candidatePoolStatus(pool.status);
        return <StatusBadge tone={status.tone}>{status.label}</StatusBadge>;
      },
    },
    {
      title: "负责人",
      key: "owner",
      width: 160,
      render: (_: unknown, pool: CandidatePool) => <Owner pool={pool} />,
    },
    {
      title: "更新时间",
      key: "updated_at",
      width: 168,
      render: (_: unknown, pool: CandidatePool) =>
        candidateDateTime(pool.updated_at),
    },
    {
      title: "操作",
      key: "action",
      width: 104,
      render: (_: unknown, pool: CandidatePool) => (
        <Link
          className="candidate-table-action"
          href={`/candidate-pools/${encodeURIComponent(pool.id)}`}
        >
          查看详情
        </Link>
      ),
    },
  ];
  return (
    <section
      className="candidate-pool-workspace campaign-workspace"
      aria-label="候选池"
    >
      {holder}
      <div className="candidate-run-history-header">
        <div>
          <h2 className="page-title">候选池</h2>
          <Text type="secondary">创建并查看用于筛选目标达人的候选池。</Text>
        </div>
        {canCreate ? (
          <Button type="primary" onClick={() => setOpen(true)}>
            创建卖家候选池
          </Button>
        ) : null}
      </div>
      <Card
        className="candidate-pool-list-card campaign-list-card"
        variant="borderless"
      >
        {query.isPending ? (
          <Skeleton active paragraph={{ rows: 7 }} />
        ) : query.isError || previewState === "error" ? (
          <Alert
            type="error"
            showIcon
            title="候选池加载失败"
            description="请稍后重试。"
            action={
              <Button onClick={() => void query.refetch()}>重新加载</Button>
            }
          />
        ) : previewState === "empty" || pools.length === 0 ? (
          <div className="campaign-empty-state">
            <AppEmpty description="暂无候选池" />
            <Text type="secondary">当前没有可查看的候选池。</Text>
          </div>
        ) : (
          <>
            <div className="campaign-table-shell">
              <Table<CandidatePool>
                className="candidate-pool-table campaign-table"
                rowKey="id"
                columns={columns}
                dataSource={pools}
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
      <Modal
        title="创建卖家候选池"
        open={open}
        footer={null}
        width={680}
        onCancel={() => !mutation.isPending && setOpen(false)}
      >
        <Input
          aria-label="候选池名称"
          value={name}
          maxLength={200}
          placeholder="候选池名称"
          onChange={(event) => {
            setName(event.target.value);
            setRetry(null);
          }}
          style={{ marginBottom: 16 }}
        />
        <SellerRuleBuilder
          submitLabel="创建并保存规则"
          loading={mutation.isPending}
          error={error}
          onSubmit={create}
        />
      </Modal>
    </section>
  );
}
