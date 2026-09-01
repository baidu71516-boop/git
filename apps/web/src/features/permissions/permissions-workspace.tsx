"use client";

import { PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";

import { ApiClientError, apiRequest } from "@/lib/api/client";

const { Paragraph } = Typography;

type Role = "super_admin" | "manager" | "operator" | "viewer";
type OperatorStatus = "active" | "disabled";
type ModuleKey =
  | "today_outreach"
  | "campaigns"
  | "candidate_pools"
  | "influencer_library"
  | "data_collection"
  | "import_history"
  | "data_updates"
  | "admin";

type OperatorAdmin = {
  id: string;
  name: string;
  role: Role;
  status: OperatorStatus;
  module_grants: ModuleKey[];
  created_at: string;
  updated_at: string;
};

type OperatorValues = {
  name: string;
  role: Role;
  status?: OperatorStatus;
  module_grants: ModuleKey[];
};

const modules: Array<{ key: ModuleKey; label: string }> = [
  { key: "today_outreach", label: "今日触达" },
  { key: "campaigns", label: "拓客活动" },
  { key: "candidate_pools", label: "候选池" },
  { key: "influencer_library", label: "达人库" },
  { key: "data_collection", label: "数据采集" },
  { key: "import_history", label: "导入记录" },
  { key: "data_updates", label: "数据更新" },
  { key: "admin", label: "权限管理" },
];

const roleLabels: Record<Role, string> = {
  super_admin: "超级管理员",
  manager: "管理员",
  operator: "操作员",
  viewer: "只读成员",
};

function formatUpdatedAt(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function errorMessage(error: unknown): string {
  if (!(error instanceof ApiClientError)) {
    return error instanceof Error ? error.message : "请求失败，请稍后重试";
  }
  const messages: Record<string, string> = {
    VERSION_CONFLICT: "数据已被其他操作更新，已重新加载最新数据。",
    LAST_SUPER_ADMIN: "不能禁用或降级最后一位启用的超级管理员。",
    ROLE_CEILING_EXCEEDED: "所选角色超过部门权限上限。",
    SUPER_ADMIN_PROMOTION_DENIED: "当前操作不允许提升为超级管理员。",
    ADMIN_MODULE_NOT_GRANTABLE: "权限管理模块不能授予非超级管理员。",
    INVALID_SESSION: "当前会话已失效，请重新登录。",
    PERMISSION_DENIED: "当前身份无权执行此操作。",
  };
  return messages[error.code ?? ""] ?? error.message;
}

function persistedGrants(values: OperatorValues): ModuleKey[] {
  if (values.role === "super_admin") return [];
  return values.module_grants.filter((module) => module !== "admin");
}

function ModuleMatrix({ role }: { role: Role | undefined }) {
  const superAdmin = role === "super_admin";
  return (
    <>
      {superAdmin ? (
        <Paragraph type="secondary">
          超级管理员拥有全部模块权限；不会保存单独模块授权。
        </Paragraph>
      ) : role === "viewer" ? (
        <Paragraph type="secondary">Viewer 对已授权模块仅可查看</Paragraph>
      ) : null}
      <Form.Item name="module_grants" label="模块权限" valuePropName="value">
        <Checkbox.Group className="permissions-module-matrix">
          <Space orientation="vertical">
            {modules.map((module) => {
              const fixed = superAdmin || module.key === "admin";
              return (
                <Checkbox
                  key={module.key}
                  value={module.key}
                  disabled={fixed}
                  checked={superAdmin ? true : undefined}
                >
                  {module.label}
                  {module.key === "admin" && !superAdmin ? "（固定关闭）" : ""}
                </Checkbox>
              );
            })}
          </Space>
        </Checkbox.Group>
      </Form.Item>
    </>
  );
}

export function PermissionsWorkspace({
  onAuthRefresh,
}: {
  onAuthRefresh: () => Promise<void>;
}) {
  const [operators, setOperators] = useState<OperatorAdmin[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<OperatorAdmin | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<OperatorValues>();

  const loadOperators = useCallback(async (clearError = true) => {
    setLoading(true);
    try {
      const response = await apiRequest<OperatorAdmin[]>("/admin/operators");
      setOperators(response.data ?? []);
      if (clearError) setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void loadOperators());
  }, [loadOperators]);

  async function openEdit(operator: OperatorAdmin) {
    setError(null);
    try {
      const response = await apiRequest<OperatorAdmin>(
        `/admin/operators/${encodeURIComponent(operator.id)}`,
      );
      if (!response.data) throw new Error("操作人详情为空");
      setEditing(response.data);
      form.setFieldsValue({
        name: response.data.name,
        role: response.data.role,
        status: response.data.status,
        module_grants: response.data.module_grants,
      });
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  function openCreate() {
    setEditing(null);
    form.setFieldsValue({
      name: "",
      role: "operator",
      module_grants: [],
    });
    setCreateOpen(true);
  }

  async function save() {
    const values = await form.validateFields();
    setSaving(true);
    setError(null);
    try {
      if (editing) {
        await apiRequest<OperatorAdmin>(`/admin/operators/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            expected_updated_at: editing.updated_at,
            name: values.name,
            role: values.role,
            status: values.status,
            module_grants: persistedGrants(values),
          }),
        });
      } else {
        await apiRequest<OperatorAdmin>("/admin/operators", {
          method: "POST",
          body: JSON.stringify({
            name: values.name,
            role: values.role,
            module_grants: persistedGrants(values),
          }),
        });
      }
      setCreateOpen(false);
      setEditing(null);
      await loadOperators();
      await onAuthRefresh();
    } catch (caught) {
      const message = errorMessage(caught);
      setError(message);
      if (
        caught instanceof ApiClientError &&
        caught.code === "VERSION_CONFLICT"
      ) {
        await loadOperators(false);
        setEditing(null);
      }
    } finally {
      setSaving(false);
    }
  }

  const formRole = Form.useWatch("role", form);
  const columns: ColumnsType<OperatorAdmin> = [
    { title: "姓名", dataIndex: "name" },
    {
      title: "角色",
      dataIndex: "role",
      render: (role: Role) => roleLabels[role],
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (status: OperatorStatus) => (
        <Tag color={status === "active" ? "green" : "default"}>
          {status === "active" ? "启用" : "已禁用"}
        </Tag>
      ),
    },
    {
      title: "模块权限",
      dataIndex: "module_grants",
      render: (grants: ModuleKey[], operator) =>
        operator.role === "super_admin"
          ? "全部模块（隐式）"
          : grants
              .map(
                (grant) =>
                  modules.find((module) => module.key === grant)?.label ??
                  grant,
              )
              .join("、") || "无",
    },
    { title: "更新时间", dataIndex: "updated_at", render: formatUpdatedAt },
    {
      title: "操作",
      key: "action",
      render: (_, operator) => (
        <Button onClick={() => void openEdit(operator)}>编辑</Button>
      ),
    },
  ];

  const modalOpen = createOpen || editing !== null;
  return (
    <section aria-label="权限管理">
      <Space direction="vertical" size="middle" className="full-width">
        <div>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建操作人
          </Button>
        </div>
        {error ? <Alert type="error" showIcon message={error} /> : null}
        <Card>
          <Table<OperatorAdmin>
            rowKey="id"
            columns={columns}
            dataSource={operators}
            loading={loading}
            pagination={false}
            locale={{ emptyText: "暂无操作人" }}
          />
        </Card>
      </Space>
      <Modal
        title={editing ? "编辑操作人" : "新建操作人"}
        open={modalOpen}
        onCancel={() => {
          setCreateOpen(false);
          setEditing(null);
        }}
        onOk={() => void save()}
        confirmLoading={saving}
        okText="保存"
      >
        <Form<OperatorValues> form={form} layout="vertical">
          <Form.Item
            label="姓名"
            name="name"
            rules={[{ required: true, message: "请输入姓名" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item label="角色" name="role" rules={[{ required: true }]}>
            <Select
              options={Object.entries(roleLabels).map(([value, label]) => ({
                value,
                label,
              }))}
              onChange={(role: Role) => {
                if (role === "super_admin")
                  form.setFieldValue("module_grants", []);
              }}
            />
          </Form.Item>
          {editing ? (
            <Form.Item label="状态" name="status" rules={[{ required: true }]}>
              <Select
                options={[
                  { value: "active", label: "启用" },
                  { value: "disabled", label: "禁用" },
                ]}
              />
            </Form.Item>
          ) : (
            <Paragraph type="secondary">新建操作人默认为启用状态。</Paragraph>
          )}
          <ModuleMatrix role={formRole} />
        </Form>
      </Modal>
    </section>
  );
}
