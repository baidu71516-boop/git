"use client";

import { LockOutlined, TeamOutlined, UserOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useCallback, useEffect, useState } from "react";

import { ApiClientError, apiRequest } from "@/lib/api/client";
import { ImportWorkspace } from "@/components/import-workspace";

const { Paragraph, Text, Title } = Typography;

type Role = "super_admin" | "manager" | "operator" | "viewer";

type Department = {
  id: string;
  name: string;
  status: "active" | "disabled";
};

type Operator = {
  id: string;
  department_id: string;
  name: string;
  role: Role;
  status: "active" | "disabled";
};

type AuthMe = {
  department: Department;
  operator: Operator | null;
  role: Role;
  expires_at: string;
};

type LoginValues = {
  department_id: string;
  password: string;
  remember_me: boolean;
};

const roleLabels: Record<Role, string> = {
  super_admin: "Super Admin",
  manager: "Manager",
  operator: "Operator",
  viewer: "Viewer",
};

export function AuthShell() {
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [auth, setAuth] = useState<AuthMe | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadDepartments = useCallback(async () => {
    const response = await apiRequest<Department[]>("/departments");
    setDepartments(response.data ?? []);
  }, []);

  const loadOperators = useCallback(async () => {
    const response = await apiRequest<Operator[]>("/operators");
    setOperators(response.data ?? []);
  }, []);

  useEffect(() => {
    async function initialize() {
      try {
        const response = await apiRequest<AuthMe>("/auth/me");
        setAuth(response.data);
        if (!response.data?.operator) {
          await loadOperators();
        }
      } catch (caught) {
        if (!(caught instanceof ApiClientError) || caught.status !== 401) {
          setError(caught instanceof Error ? caught.message : "系统暂时不可用");
        }
        await loadDepartments();
      } finally {
        setLoading(false);
      }
    }
    void initialize();
  }, [loadDepartments, loadOperators]);

  async function handleLogin(values: LoginValues) {
    setSubmitting(true);
    setError(null);
    try {
      await apiRequest("/auth/login", {
        method: "POST",
        body: JSON.stringify(values),
      });
      const meResponse = await apiRequest<AuthMe>("/auth/me");
      setAuth(meResponse.data);
      await loadOperators();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleOperatorSelect(operatorId: string) {
    setSubmitting(true);
    setError(null);
    try {
      const response = await apiRequest<AuthMe>("/auth/select-operator", {
        method: "POST",
        body: JSON.stringify({ operator_id: operatorId }),
      });
      setAuth(response.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "选择操作人失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleLogout() {
    setSubmitting(true);
    try {
      await apiRequest("/auth/logout", { method: "POST" });
      setAuth(null);
      setOperators([]);
      await loadDepartments();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "退出失败");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <main className="auth-shell auth-loading">
        <Spin size="large" description="正在检查登录状态" />
      </main>
    );
  }

  if (!auth) {
    return (
      <main className="auth-shell">
        <Card className="login-card" bordered={false}>
          <Space direction="vertical" size="large" className="full-width">
            <div>
              <Tag color="blue">INTERNAL</Tag>
              <Title level={2}>达人智能触达系统</Title>
              <Paragraph type="secondary">请使用部门密码登录。</Paragraph>
            </div>
            {error ? <Alert type="error" showIcon message={error} /> : null}
            <Form<LoginValues>
              layout="vertical"
              initialValues={{ remember_me: false }}
              onFinish={(values) => void handleLogin(values)}
            >
              <Form.Item
                label="部门"
                name="department_id"
                rules={[{ required: true, message: "请选择部门" }]}
              >
                <Select
                  placeholder="选择部门"
                  options={departments.map((department) => ({
                    label: department.name,
                    value: department.id,
                  }))}
                  suffixIcon={<TeamOutlined />}
                />
              </Form.Item>
              <Form.Item
                label="密码"
                name="password"
                rules={[{ required: true, message: "请输入密码" }]}
              >
                <Input.Password
                  prefix={<LockOutlined />}
                  autoComplete="current-password"
                />
              </Form.Item>
              <Form.Item name="remember_me" valuePropName="checked">
                <Checkbox>30 天内保持登录</Checkbox>
              </Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                block
                loading={submitting}
              >
                登录
              </Button>
            </Form>
          </Space>
        </Card>
      </main>
    );
  }

  return (
    <main className="auth-shell app-authenticated">
      <Card className="identity-card" bordered={false}>
        <Space direction="vertical" size="large" className="full-width">
          <div className="workspace-heading">
            <div>
              <Tag color="success">登录成功</Tag>
              <Title level={3}>当前身份</Title>
            </div>
            <Button onClick={() => void handleLogout()} loading={submitting}>
              退出登录
            </Button>
          </div>
          {error ? <Alert type="error" showIcon message={error} /> : null}
          <Descriptions bordered column={1}>
            <Descriptions.Item label="当前部门">
              {auth.department.name}
            </Descriptions.Item>
            <Descriptions.Item label="当前操作人">
              {auth.operator?.name ?? "待选择"}
            </Descriptions.Item>
            <Descriptions.Item label="当前角色">
              <Tag color="blue">{roleLabels[auth.role]}</Tag>
            </Descriptions.Item>
          </Descriptions>
          <Text type="secondary">
            <UserOutlined /> 选择操作人仅改变操作归属，不会改变 Session 权限。
          </Text>
        </Space>
      </Card>

      {auth.operator ? <ImportWorkspace role={auth.role} /> : null}

      <Modal
        title="选择当前操作人"
        open={!auth.operator}
        footer={null}
        closable={false}
      >
        <Paragraph type="secondary">请选择本次操作的实际执行人。</Paragraph>
        {error ? (
          <Alert
            type="error"
            showIcon
            message={error}
            className="modal-error"
          />
        ) : null}
        <Select
          className="full-width"
          size="large"
          placeholder="选择操作人"
          loading={submitting}
          onChange={(operatorId: string) =>
            void handleOperatorSelect(operatorId)
          }
          options={operators.map((operator) => ({
            label: `${operator.name} · ${roleLabels[operator.role]}`,
            value: operator.id,
          }))}
        />
      </Modal>
    </main>
  );
}
