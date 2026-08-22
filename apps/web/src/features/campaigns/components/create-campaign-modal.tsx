"use client";

import { Alert, Button, Form, Input, Modal, Select } from "antd";
import { useRef, useState } from "react";

import { ApiClientError } from "@/lib/api/client";

import {
  campaignMutationErrorMessage,
  isAmbiguousCampaignMutationError,
} from "../formatters";
import { useCampaignOperators, useCreateCampaignMutation } from "../queries";
import type { Campaign, CampaignScope, CreateCampaignInput } from "../types";

type CreateAttempt = { input: CreateCampaignInput; key: string };

function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

function validationField(error: ApiClientError): "name" | "owner_operator_id" {
  const detail = Array.isArray(error.details) ? error.details[0] : null;
  const location =
    detail && typeof detail === "object" && "loc" in detail
      ? (detail as { loc?: unknown }).loc
      : null;
  return Array.isArray(location) && location.includes("owner_operator_id")
    ? "owner_operator_id"
    : "name";
}

export function CreateCampaignModal({
  open,
  canMutate,
  scope,
  onClose,
  onCreated,
}: {
  open: boolean;
  canMutate: boolean;
  scope?: CampaignScope;
  onClose: () => void;
  onCreated: (campaign: Campaign) => void;
}) {
  const [form] = Form.useForm<CreateCampaignInput>();
  const [error, setError] = useState<string | null>(null);
  const [retryAttempt, setRetryAttempt] = useState<CreateAttempt | null>(null);
  const operatorsQuery = useCampaignOperators(scope, open);
  const createMutation = useCreateCampaignMutation(scope);
  const activeAttempt = useRef<CreateAttempt | null>(null);

  function resetAttempt() {
    form.resetFields();
    activeAttempt.current = null;
    setError(null);
    setRetryAttempt(null);
  }

  function close() {
    resetAttempt();
    onClose();
  }

  function clearRetryIfChanged() {
    if (!retryAttempt) return;
    const current = form.getFieldsValue();
    if (JSON.stringify(current) !== JSON.stringify(retryAttempt.input)) {
      activeAttempt.current = null;
      setRetryAttempt(null);
      setError(null);
    }
  }

  async function submit(input: CreateCampaignInput, attempt?: CreateAttempt) {
    if (!canMutate) return;
    const nextAttempt = attempt ?? { input, key: newIdempotencyKey() };
    activeAttempt.current = nextAttempt;
    setError(null);
    try {
      const campaign = await createMutation.mutateAsync({
        input: nextAttempt.input,
        idempotencyKey: nextAttempt.key,
      });
      resetAttempt();
      onCreated(campaign);
    } catch (caught) {
      if (isAmbiguousCampaignMutationError(caught)) {
        setRetryAttempt(nextAttempt);
        setError("创建结果暂时无法确认。你可以重试本次创建。");
        return;
      }
      activeAttempt.current = null;
      setRetryAttempt(null);
      if (caught instanceof ApiClientError && caught.status === 422) {
        form.setFields([
          {
            name: validationField(caught),
            errors: ["提交内容有误，请检查后重试。"],
          },
        ]);
      }
      setError(
        campaignMutationErrorMessage(caught, "创建活动失败，请稍后重试。"),
      );
    }
  }

  return (
    <Modal
      title="新建拓客活动"
      open={open}
      width={560}
      destroyOnHidden
      onCancel={close}
      footer={null}
    >
      {error ? (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            retryAttempt ? (
              <Button
                size="small"
                onClick={() => void submit(retryAttempt.input, retryAttempt)}
              >
                重试本次创建
              </Button>
            ) : null
          }
          className="campaign-modal-alert"
        />
      ) : null}
      {operatorsQuery.isError ? (
        <Alert
          type="warning"
          showIcon
          message="负责人选项加载失败"
          description="可以不指定负责人创建，或重试加载负责人。"
          action={
            <Button size="small" onClick={() => void operatorsQuery.refetch()}>
              重新加载
            </Button>
          }
          className="campaign-modal-alert"
        />
      ) : null}
      <Form<CreateCampaignInput>
        form={form}
        layout="vertical"
        onFinish={(values) => void submit(values)}
        onValuesChange={clearRetryIfChanged}
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
        <Form.Item
          label="负责人"
          name="owner_operator_id"
          extra="未指定负责人时，将由系统按当前操作人设置。"
        >
          <Select
            allowClear
            placeholder="未指定"
            loading={operatorsQuery.isLoading}
            options={(operatorsQuery.data ?? []).map((operator) => ({
              label: operator.name,
              value: operator.id,
            }))}
          />
        </Form.Item>
        <div className="campaign-modal-footer">
          <Button onClick={close}>取消</Button>
          <Button
            type="primary"
            htmlType="submit"
            loading={createMutation.isPending}
            disabled={!canMutate}
          >
            创建活动
          </Button>
        </div>
      </Form>
    </Modal>
  );
}
