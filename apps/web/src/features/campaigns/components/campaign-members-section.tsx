"use client";

import { ReloadOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Modal, Typography, message } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { AppEmpty } from "@/components/ui/app-empty";
import { ApiClientError } from "@/lib/api/client";

import { AddCampaignMembersDrawer } from "./add-campaign-members-drawer";
import { CampaignMemberTable } from "./campaign-member-table";
import {
  campaignQueryKeys,
  useBulkAddCampaignMembersMutation,
  useCampaignMembers,
  useRemoveCampaignMemberMutation,
} from "../queries";
import { isAmbiguousCampaignMutationError } from "../formatters";
import type {
  Campaign,
  CampaignMember,
  CampaignMemberAddItem,
  CampaignRole,
  CampaignScope,
} from "../types";

const { Text } = Typography;

function mutationError(error: unknown, fallback: string): string {
  if (!(error instanceof ApiClientError)) return fallback;
  const messages: Record<string, string> = {
    PREFERRED_PLATFORM_ACCOUNT_INVALID:
      "所选平台账号已不可用或与达人不匹配。请重新选择后再提交。",
    IDEMPOTENCY_KEY_REUSED: "本次添加请求标识已被使用，请重新发起添加。",
    OPERATOR_REQUIRED: "请先选择当前操作人。",
    PERMISSION_DENIED: "没有执行此操作的权限。",
    CAMPAIGN_CLOSED: "该拓客活动已关闭，无法调整活动达人。",
  };
  return error.code && messages[error.code] ? messages[error.code] : fallback;
}

export function CampaignMembersSection({
  campaign,
  role,
  hasSelectedOperator,
  scope,
}: {
  campaign: Campaign;
  role: CampaignRole;
  hasSelectedOperator: boolean;
  scope?: CampaignScope;
}) {
  const [messageApi, contextHolder] = message.useMessage();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [memberToRemove, setMemberToRemove] = useState<CampaignMember | null>(
    null,
  );
  const memberQuery = useCampaignMembers(campaign.id, scope, true);
  const bulkAddMutation = useBulkAddCampaignMembersMutation(scope);
  const removeMutation = useRemoveCampaignMemberMutation(scope);
  const queryClient = useQueryClient();
  const members = useMemo(
    () => memberQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [memberQuery.data],
  );
  const statusAllowsMutation = ["DRAFT", "ACTIVE", "PAUSED"].includes(
    campaign.status,
  );
  const canMutate =
    role !== "viewer" && hasSelectedOperator && statusAllowsMutation;

  async function reloadFirstMemberPage() {
    await queryClient.resetQueries({
      queryKey: campaignQueryKeys.members(campaign.id, scope),
    });
  }

  async function loadMore() {
    try {
      await memberQuery.fetchNextPage();
    } catch (caught) {
      if (
        caught instanceof ApiClientError &&
        caught.code === "CURSOR_MISMATCH"
      ) {
        await reloadFirstMemberPage();
        void messageApi.info("活动达人列表已更新，已重新加载。");
      }
    }
  }

  async function submitMembers(
    input: CampaignMemberAddItem[],
    idempotencyKey: string,
  ): Promise<"retry" | "new"> {
    setSubmitError(null);
    try {
      const result = await bulkAddMutation.mutateAsync({
        campaignId: campaign.id,
        members: input,
        idempotencyKey,
      });
      setDrawerOpen(false);
      await reloadFirstMemberPage();
      void messageApi.success(
        <span>
          达人添加完成：新增 {result.added_count} · 重新加入{" "}
          {result.restored_count} · 已在活动中 {result.already_active_count}
          {result.already_active_count > 0
            ? "。已在活动中的达人未做修改。"
            : ""}
        </span>,
      );
      return "new";
    } catch (caught) {
      setSubmitError(mutationError(caught, "添加活动达人失败，请稍后重试。"));
      return isAmbiguousCampaignMutationError(caught) ? "retry" : "new";
    }
  }

  async function removeMember() {
    if (!memberToRemove) return;
    const removedMember = memberToRemove;
    try {
      await removeMutation.mutateAsync({
        campaignId: campaign.id,
        member: removedMember,
      });
      setMemberToRemove(null);
      await reloadFirstMemberPage();
      void messageApi.success("达人已移出活动");
    } catch (caught) {
      setMemberToRemove(null);
      if (
        caught instanceof ApiClientError &&
        caught.code === "VERSION_CONFLICT"
      ) {
        await reloadFirstMemberPage();
        const refreshed = queryClient.getQueryData<{
          pages: Array<{ items: CampaignMember[] }>;
        }>(campaignQueryKeys.members(campaign.id, scope));
        const stillPresent = refreshed?.pages
          .flatMap((page) => page.items)
          .some((member) => member.id === removedMember.id);
        void messageApi.info(
          stillPresent
            ? "该达人的活动成员状态已发生变化，列表已更新。"
            : "该达人已不在当前活动中，列表已更新。",
        );
        return;
      }
      void messageApi.error(
        mutationError(caught, "移出活动失败，请稍后重试。"),
      );
    }
  }

  return (
    <>
      {contextHolder}
      <Card
        className="campaign-detail-card campaign-members-card"
        variant="borderless"
        title="活动达人"
        extra={
          canMutate ? (
            <Button
              type="primary"
              icon={<PlusOutlined aria-hidden="true" />}
              onClick={() => setDrawerOpen(true)}
            >
              添加达人
            </Button>
          ) : null
        }
      >
        {campaign.status === "CLOSED" ? (
          <Text type="secondary">活动已关闭，无法调整活动达人。</Text>
        ) : null}
        {memberQuery.isPending ? (
          <CampaignMemberTable loading />
        ) : memberQuery.isError ? (
          <Alert
            type="error"
            showIcon
            title="活动达人加载失败"
            description="请稍后重试。"
            action={
              <Button onClick={() => void memberQuery.refetch()}>
                重新加载
              </Button>
            }
          />
        ) : members.length === 0 ? (
          <div className="campaign-empty-state">
            <AppEmpty description="暂无活动达人" />
            <Text type="secondary">当前还没有达人加入这个拓客活动。</Text>
            {canMutate ? (
              <Button type="primary" onClick={() => setDrawerOpen(true)}>
                添加达人
              </Button>
            ) : null}
          </div>
        ) : (
          <>
            <CampaignMemberTable
              items={members}
              canRemove={canMutate}
              onRemove={setMemberToRemove}
            />
            <div className="campaign-pagination-footer">
              {memberQuery.hasNextPage ? (
                <Button
                  icon={<ReloadOutlined aria-hidden="true" />}
                  loading={memberQuery.isFetchingNextPage}
                  onClick={() => void loadMore()}
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
      {canMutate && drawerOpen ? (
        <AddCampaignMembersDrawer
          open
          submitting={bulkAddMutation.isPending}
          submitError={submitError}
          onClose={() => {
            setDrawerOpen(false);
            setSubmitError(null);
          }}
          onSubmit={submitMembers}
        />
      ) : null}
      <Modal
        title="将达人移出活动？"
        open={memberToRemove !== null}
        okText="确认移出"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={removeMutation.isPending}
        onCancel={() => !removeMutation.isPending && setMemberToRemove(null)}
        onOk={() => void removeMember()}
      >
        <p>移出后，该达人将不再属于当前拓客活动。不会删除达人资料。</p>
        <Text strong>{memberToRemove?.influencer.display_name}</Text>
      </Modal>
    </>
  );
}
