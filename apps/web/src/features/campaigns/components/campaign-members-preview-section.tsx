"use client";

import { ReloadOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Modal, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";

import { AppEmpty } from "@/components/ui/app-empty";

import { AddCampaignMembersDrawer } from "./add-campaign-members-drawer";
import { CampaignMemberTable } from "./campaign-member-table";
import type {
  CampaignMemberPreviewRemoveOutcome,
  CampaignMembersPreview,
} from "../preview-types";
import type { Campaign, CampaignMember, CampaignRole } from "../types";

const { Text } = Typography;

export function CampaignMembersPreviewSection({
  campaign,
  role,
  hasSelectedOperator,
  preview,
}: {
  campaign: Campaign;
  role: CampaignRole;
  hasSelectedOperator: boolean;
  preview: CampaignMembersPreview;
}) {
  const [messageApi, contextHolder] = message.useMessage();
  const [mounted, setMounted] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(
    preview.initialDrawerOpen ?? false,
  );
  const [drawerError, setDrawerError] = useState<string | null>(null);
  const [items, setItems] = useState(preview.items);
  const [nextCursor, setNextCursor] = useState(preview.nextCursor);
  const [memberToRemove, setMemberToRemove] = useState<CampaignMember | null>(
    preview.removeOutcome ? (preview.items[0] ?? null) : null,
  );
  const [removeSubmitting, setRemoveSubmitting] = useState(false);
  const canMutate =
    role !== "viewer" &&
    hasSelectedOperator &&
    ["DRAFT", "ACTIVE", "PAUSED"].includes(campaign.status);
  const canShowDrawer = canMutate && Boolean(preview.addDrawer);
  const state = preview.state;

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setMounted(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const emptyActions = canShowDrawer ? (
    <Button type="primary" onClick={() => setDrawerOpen(true)}>
      添加达人
    </Button>
  ) : null;

  function handleRemove() {
    if (!memberToRemove) return;
    const target = memberToRemove;
    const outcome: CampaignMemberPreviewRemoveOutcome =
      preview.removeOutcome ?? "success";
    setRemoveSubmitting(true);
    window.setTimeout(() => {
      setRemoveSubmitting(false);
      setMemberToRemove(null);
      if (outcome === "success") {
        setItems((current) => current.filter((item) => item.id !== target.id));
        void messageApi.success("达人已移出活动");
        return;
      }
      if (outcome === "conflict-removed") {
        setItems((current) => current.filter((item) => item.id !== target.id));
        void messageApi.info("该达人已不在当前活动中，列表已更新。");
        return;
      }
      void messageApi.info("该达人的活动成员状态已发生变化，列表已更新。");
    }, 240);
  }

  const members = useMemo(() => items, [items]);

  return (
    <>
      {contextHolder}
      <Card
        className="campaign-detail-card campaign-members-card campaign-members-preview-card"
        variant="borderless"
        title={
          <div className="campaign-members-heading">
            <div className="campaign-members-title">活动达人</div>
            <Text type="secondary">
              管理当前活动中的达人及其使用的平台账号。
            </Text>
          </div>
        }
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
          <div className="campaign-members-closed-note">
            活动已关闭，无法调整活动达人。
          </div>
        ) : null}
        {state === "error" ? (
          <Alert
            type="error"
            showIcon
            title="活动达人加载失败"
            description="请稍后重试。"
            action={<Button onClick={() => undefined}>重新加载</Button>}
          />
        ) : state === "empty" || members.length === 0 ? (
          <div className="campaign-empty-state campaign-members-empty-state">
            <AppEmpty description="暂无活动达人" />
            <Text type="secondary">当前还没有达人加入这个拓客活动。</Text>
            {emptyActions}
          </div>
        ) : (
          <>
            <div className="campaign-member-table-shell">
              <CampaignMemberTable
                items={members}
                canRemove={canMutate}
                onRemove={setMemberToRemove}
              />
            </div>
            <div className="campaign-pagination-footer campaign-members-pagination">
              {nextCursor ? (
                <Button
                  icon={<ReloadOutlined aria-hidden="true" />}
                  onClick={() => setNextCursor(null)}
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
      {mounted && canShowDrawer && preview.addDrawer ? (
        <AddCampaignMembersDrawer
          open={drawerOpen}
          submitting={false}
          submitError={drawerError}
          onClose={() => {
            setDrawerOpen(false);
            setDrawerError(null);
          }}
          preview={preview.addDrawer}
        />
      ) : null}
      {mounted ? (
        <Modal
          title="将达人移出活动？"
          open={memberToRemove !== null}
          okText="确认移出"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          confirmLoading={removeSubmitting}
          onCancel={() => !removeSubmitting && setMemberToRemove(null)}
          onOk={handleRemove}
        >
          <p>移出后，该达人将不再属于当前拓客活动。不会删除达人资料。</p>
          <Text strong>{memberToRemove?.influencer.display_name}</Text>
        </Modal>
      ) : null}
    </>
  );
}
