"use client";

import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Typography,
} from "antd";

import { buildCampaignUpdateInput } from "../api";
import {
  campaignMutationErrorMessage,
  isDisabledCampaignOwner,
} from "../formatters";
import { useCampaignOperators, useUpdateCampaignMutation } from "../queries";
import type { Campaign, CampaignEditableValues, CampaignScope } from "../types";
import { ApiClientError } from "@/lib/api/client";

const { Text } = Typography;

export function EditCampaignModal({
  open,
  campaign,
  canMutate,
  scope,
  onClose,
  onUpdated,
  onVersionConflict,
  onCampaignClosed,
}: {
  open: boolean;
  campaign: Campaign;
  canMutate: boolean;
  scope?: CampaignScope;
  onClose: () => void;
  onUpdated: (campaign: Campaign) => void;
  onVersionConflict: () => void;
  onCampaignClosed: () => void;
}) {
  const [form] = Form.useForm<CampaignEditableValues>();
  const operatorsQuery = useCampaignOperators(scope, open);
  const updateMutation = useUpdateCampaignMutation(scope);
  const currentOwnerDisabled = isDisabledCampaignOwner(campaign.owner);
  const currentOwnerOption = {
    label: currentOwnerDisabled
      ? `${campaign.owner.name}（已停用）`
      : campaign.owner.name,
    value: campaign.owner_operator_id,
    disabled: currentOwnerDisabled,
  };
  const activeOptions = (operatorsQuery.data ?? []).map((operator) => ({
    label: operator.name,
    value: operator.id,
  }));
  const options = activeOptions.some(
    (option) => option.value === currentOwnerOption.value,
  )
    ? activeOptions
    : [currentOwnerOption, ...activeOptions];

  async function submit(values: CampaignEditableValues) {
    try {
      const updated = await updateMutation.mutateAsync({
        campaignId: campaign.id,
        input: buildCampaignUpdateInput(campaign, values),
      });
      onUpdated(updated);
    } catch (caught) {
      const code =
        caught instanceof Error && "code" in caught
          ? (caught as { code?: string }).code
          : null;
      if (code === "VERSION_CONFLICT") {
        onVersionConflict();
        return;
      }
      if (code === "CAMPAIGN_CLOSED") {
        onCampaignClosed();
        return;
      }
      const field =
        caught instanceof ApiClientError &&
        Array.isArray(caught.details) &&
        (caught.details[0] as { loc?: unknown } | undefined)?.loc instanceof
          Array &&
        (caught.details[0] as { loc: unknown[] }).loc.includes(
          "owner_operator_id",
        )
          ? "owner_operator_id"
          : "name";
      form.setFields([
        {
          name: field,
          errors: [
            campaignMutationErrorMessage(caught, "编辑活动失败，请稍后重试."),
          ],
        },
      ]);
    }
  }

  return (
    <Modal
      title="编辑拓客活动"
      open={open}
      width={560}
      destroyOnHidden
      onCancel={onClose}
      footer={null}
    >
      {operatorsQuery.isError ? (
        <Alert
          type="warning"
          showIcon
          message="负责人选项加载失败"
          description="当前负责人仍可保留，暂时无法变更负责人。"
          action={
            <Button size="small" onClick={() => void operatorsQuery.refetch()}>
              重新加载
            </Button>
          }
          className="campaign-modal-alert"
        />
      ) : null}
      <Form<CampaignEditableValues>
        form={form}
        layout="vertical"
        initialValues={{
          name: campaign.name,
          owner_operator_id: campaign.owner_operator_id,
        }}
        onFinish={(values) => void submit(values)}
      >
        <Form.Item
          label="活动名称"
          name="name"
          rules={[
            { required: true, message: "请输入活动名称" },
            { max: 200, message: "活动名称不能超过 200 个字符" },
          ]}
        >
          <Input autoFocus maxLength={200} />
        </Form.Item>
        <Form.Item label="负责人" name="owner_operator_id">
          {operatorsQuery.isError ? (
            <Space direction="vertical" size={0}>
              <Text>{campaign.owner.name}</Text>
              {currentOwnerDisabled ? (
                <Text type="secondary">已停用</Text>
              ) : null}
            </Space>
          ) : (
            <Select loading={operatorsQuery.isLoading} options={options} />
          )}
        </Form.Item>
        <div className="campaign-modal-footer">
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            htmlType="submit"
            loading={updateMutation.isPending}
            disabled={!canMutate}
          >
            保存
          </Button>
        </div>
      </Form>
    </Modal>
  );
}
