"use client";

import { Alert, Form, InputNumber, Modal, Select } from "antd";
import { useState } from "react";

import { isAmbiguousMutationError, mutationErrorMessage } from "../formatters";
import { useCreateRefreshQueueMutation } from "../queries";
import type {
  DepartmentOption,
  RefreshQueueCreateInput,
  RefreshQueueDetail,
  RefreshQueueRole,
} from "../types";

const ambiguousCreateMessage =
  "无法确认更新名单是否创建成功，请先刷新数据更新列表确认，避免重复创建。";

export function CreateRefreshQueueModal({
  open,
  role,
  departments,
  currentDepartmentId,
  onClose,
  onCreated,
}: {
  open: boolean;
  role: RefreshQueueRole;
  departments: DepartmentOption[];
  currentDepartmentId: string;
  onClose: () => void;
  onCreated: (detail: RefreshQueueDetail) => void;
}) {
  const [form] = Form.useForm<RefreshQueueCreateInput>();
  const mutation = useCreateRefreshQueueMutation();
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const values = await form.validateFields();
    setError(null);
    const payload: RefreshQueueCreateInput = {
      requested_limit: values.requested_limit,
      refresh_limit: values.refresh_limit,
      today_total_limit: values.today_total_limit,
    };
    if (role === "super_admin" && values.department_id) {
      payload.department_id = values.department_id;
    }
    try {
      const detail = await mutation.mutateAsync(payload);
      form.resetFields();
      onCreated(detail);
    } catch (caught) {
      setError(
        isAmbiguousMutationError(caught)
          ? ambiguousCreateMessage
          : mutationErrorMessage(caught, "创建更新名单失败，请检查后重试。"),
      );
    }
  }

  return (
    <Modal
      title="创建更新名单"
      open={open}
      width={560}
      okText="创建名单"
      cancelText="返回"
      confirmLoading={mutation.isPending}
      onOk={() => void submit()}
      onCancel={onClose}
      afterClose={() => {
        form.resetFields();
        setError(null);
      }}
      destroyOnHidden
    >
      {error ? (
        <Alert
          className="refresh-queue-modal-alert"
          type="error"
          showIcon
          title={error}
        />
      ) : null}
      <Form<RefreshQueueCreateInput>
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={
          role === "super_admin"
            ? { department_id: currentDepartmentId }
            : undefined
        }
      >
        {role === "super_admin" ? (
          <Form.Item
            label="目标部门"
            name="department_id"
            rules={[{ required: true, message: "请选择目标部门" }]}
          >
            <Select
              placeholder="选择目标部门"
              options={departments.map((department) => ({
                label: department.name,
                value: department.id,
              }))}
            />
          </Form.Item>
        ) : null}
        <Form.Item
          label="目标更新数量"
          name="requested_limit"
          rules={[
            { required: true, message: "请输入目标更新数量" },
            {
              validator: (_, value: number | undefined) =>
                value !== undefined && value > 2_000
                  ? Promise.reject(new Error("目标更新数量不能超过 2000"))
                  : Promise.resolve(),
            },
          ]}
        >
          <InputNumber
            min={1}
            max={2_000}
            precision={0}
            className="full-width"
          />
        </Form.Item>
        <Form.Item
          label="本次更新上限"
          name="refresh_limit"
          dependencies={["requested_limit"]}
          rules={[
            { required: true, message: "请输入本次更新上限" },
            ({ getFieldValue }) => ({
              validator: (_, value: number | undefined) =>
                value !== undefined && value < getFieldValue("requested_limit")
                  ? Promise.reject(
                      new Error("本次更新上限不能小于目标更新数量"),
                    )
                  : Promise.resolve(),
            }),
          ]}
        >
          <InputNumber min={1} precision={0} className="full-width" />
        </Form.Item>
        <Form.Item
          label="今日计划总上限"
          name="today_total_limit"
          dependencies={["refresh_limit"]}
          rules={[
            { required: true, message: "请输入今日计划总上限" },
            ({ getFieldValue }) => ({
              validator: (_, value: number | undefined) =>
                value !== undefined && value < getFieldValue("refresh_limit")
                  ? Promise.reject(
                      new Error("今日计划总上限不能小于本次更新上限"),
                    )
                  : Promise.resolve(),
            }),
          ]}
        >
          <InputNumber min={1} precision={0} className="full-width" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
