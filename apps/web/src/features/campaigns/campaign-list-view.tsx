"use client";

import { ReloadOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Tooltip, Typography, message } from "antd";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { AppEmpty } from "@/components/ui/app-empty";
import { PageHeader } from "@/components/ui/page-header";

import { CampaignTable } from "./components/campaign-table";
import { CreateCampaignModal } from "./components/create-campaign-modal";
import { useCampaignList } from "./queries";
import type { CampaignRole, CampaignScope } from "./types";

const { Text } = Typography;

export function CampaignListView({
  role,
  hasSelectedOperator,
  scope,
}: {
  role: CampaignRole;
  hasSelectedOperator: boolean;
  scope?: CampaignScope;
}) {
  const router = useRouter();
  const [createOpen, setCreateOpen] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const listQuery = useCampaignList(scope);
  const items = useMemo(
    () => listQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [listQuery.data],
  );
  const canWrite = role !== "viewer";
  const canMutate = canWrite && hasSelectedOperator;

  const createAction = canWrite ? (
    canMutate ? (
      <Button
        type="primary"
        icon={<PlusOutlined aria-hidden="true" />}
        onClick={() => setCreateOpen(true)}
      >
        新建活动
      </Button>
    ) : (
      <Tooltip title="请先选择当前操作人">
        <Button
          type="primary"
          icon={<PlusOutlined aria-hidden="true" />}
          disabled
        >
          新建活动
        </Button>
      </Tooltip>
    )
  ) : null;

  return (
    <section className="campaign-workspace" aria-label="拓客活动">
      {contextHolder}
      <PageHeader
        title="拓客活动"
        description="管理销售拓展活动和负责人。"
        extra={createAction}
      />
      <Card className="campaign-list-card" variant="borderless">
        {listQuery.isPending ? (
          <CampaignTable loading />
        ) : listQuery.isError ? (
          <Alert
            type="error"
            showIcon
            title="拓客活动加载失败"
            description="请稍后重试。"
            action={
              <Button onClick={() => void listQuery.refetch()}>重新加载</Button>
            }
          />
        ) : items.length === 0 ? (
          <div className="campaign-empty-state">
            <AppEmpty description="暂无拓客活动" />
            <Text type="secondary">还没有创建销售拓展活动。</Text>
            {canMutate ? (
              <Button type="primary" onClick={() => setCreateOpen(true)}>
                新建活动
              </Button>
            ) : null}
          </div>
        ) : (
          <>
            <CampaignTable items={items} />
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
      {canWrite ? (
        <CreateCampaignModal
          open={createOpen}
          canMutate={canMutate}
          scope={scope}
          onClose={() => setCreateOpen(false)}
          onCreated={(campaign) => {
            setCreateOpen(false);
            void messageApi.success("拓客活动已创建");
            router.push(`/campaigns/${encodeURIComponent(campaign.id)}`);
          }}
        />
      ) : null}
    </section>
  );
}
