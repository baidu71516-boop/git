"use client";

import { LockOutlined, TeamOutlined } from "@ant-design/icons";
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
  Spin,
  Typography,
} from "antd";
import { useCallback, useEffect, useState } from "react";

import { InfluencerWorkspace } from "@/features/influencers/influencer-workspace";
import { InfluencerDetailWorkspace } from "@/features/influencers/influencer-detail-workspace";
import { DataCollectionWorkspace } from "@/features/imports/data-collection-workspace";
import { ImportJobHistory } from "@/features/imports/import-job-history";
import { RefreshQueueDetail } from "@/features/refresh-queues/refresh-queue-detail";
import { RefreshQueueList } from "@/features/refresh-queues/refresh-queue-list";
import { TodayWorkspace } from "@/features/outreach-today/today-workspace";
import { CampaignDetailView } from "@/features/campaigns/campaign-detail-view";
import { CampaignListView } from "@/features/campaigns/campaign-list-view";
import { CandidatePoolDetailView } from "@/features/candidate-pools/candidate-pool-detail-view";
import { CandidatePoolListView } from "@/features/candidate-pools/candidate-pool-list-view";
import { CandidateRunDetailView } from "@/features/candidate-pools/candidate-run-detail-view";
import { BuyerProspectWorkspace } from "@/features/buyer-prospects/buyer-prospect-workspace";
import { PermissionsWorkspace } from "@/features/permissions/permissions-workspace";
import { AppShell } from "@/components/app-shell";
import { ComplianceFooter } from "@/components/compliance-footer";
import { ApiClientError, apiRequest } from "@/lib/api/client";

const { Paragraph, Title } = Typography;

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
  // Legacy Department ceiling retained only for response compatibility. It is
  // never used by this shell as a selected Operator's business authority.
  role: Role;
  department_role_ceiling: Role;
  effective_role: Role | null;
  expires_at: string;
};

type LoginValues = {
  department_id: string;
  password: string;
  remember_me: boolean;
};

type OperatorAuthValues = {
  operator_id: string;
  operator_password: string;
};

const roleLabels: Record<Role, string> = {
  super_admin: "超级管理员",
  manager: "管理员",
  operator: "操作员",
  viewer: "只读成员",
};

type AuthWorkspace =
  | "imports"
  | "import-jobs"
  | "influencers"
  | "refresh-queues"
  | "outreach-today"
  | "campaigns"
  | "candidate-pools"
  | "buyer-prospects"
  | "permissions";

function operatorAuthErrorMessage(error: unknown): string {
  if (!(error instanceof ApiClientError)) {
    return "操作人认证失败，请稍后重试。";
  }
  const messages: Record<string, string> = {
    CREDENTIAL_SETUP_REQUIRED:
      "该操作人尚未设置个人密码，请联系管理员完成凭据初始化。",
    INVALID_OPERATOR_CREDENTIALS: "操作人或个人密码错误。",
    LOGIN_LOCKED: "尝试次数过多，请稍后重试。",
    OPERATOR_AUTH_LOCKED: "尝试次数过多，请稍后重试。",
    INVALID_SESSION: "当前会话已失效，请重新登录。",
    SESSION_EXPIRED: "当前会话已过期，请重新登录。",
    AUTH_REQUIRED: "当前会话已失效，请重新登录。",
    CSRF_FAILED: "安全校验失败，请刷新页面后重试。",
  };
  return messages[error.code ?? ""] ?? "操作人认证失败，请稍后重试。";
}

export function AuthShell({
  workspace = "imports",
  influencerId,
  refreshQueueId,
  campaignId,
  candidatePoolId,
  candidateRunId,
}: {
  workspace?: AuthWorkspace;
  influencerId?: string;
  refreshQueueId?: string;
  campaignId?: string;
  candidatePoolId?: string;
  candidateRunId?: string;
}) {
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [auth, setAuth] = useState<AuthMe | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [operatorForm] = Form.useForm<OperatorAuthValues>();
  const requiresOperator = auth !== null && auth.operator === null;

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
        if (response.data && !response.data.operator) {
          await loadOperators();
        }
        if (
          workspace === "refresh-queues" &&
          response.data?.effective_role === "super_admin"
        ) {
          await loadDepartments();
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
  }, [loadDepartments, loadOperators, workspace]);

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
      if (meResponse.data && !meResponse.data.operator) {
        await loadOperators();
      }
      if (
        workspace === "refresh-queues" &&
        meResponse.data?.effective_role === "super_admin"
      ) {
        await loadDepartments();
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleOperatorSelect(values: OperatorAuthValues) {
    setSubmitting(true);
    setError(null);
    try {
      const response = await apiRequest<AuthMe>("/auth/select-operator", {
        method: "POST",
        body: JSON.stringify({
          operator_id: values.operator_id,
          operator_password: values.operator_password,
        }),
      });
      setAuth(response.data);
      operatorForm.resetFields();
    } catch (caught) {
      operatorForm.setFieldValue("operator_password", "");
      setError(operatorAuthErrorMessage(caught));
      if (
        caught instanceof ApiClientError &&
        ["INVALID_SESSION", "SESSION_EXPIRED", "AUTH_REQUIRED"].includes(
          caught.code ?? "",
        )
      ) {
        setAuth(null);
        setOperators([]);
        try {
          await loadDepartments();
        } catch {
          // The stable session error remains actionable even if this refresh
          // also fails; the user can retry from a fresh page load.
        }
      }
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

  async function refreshAuth() {
    try {
      const response = await apiRequest<AuthMe>("/auth/me");
      setAuth(response.data);
    } catch (caught) {
      if (caught instanceof ApiClientError && caught.status === 401) {
        setAuth(null);
        setOperators([]);
        await loadDepartments();
        return;
      }
      throw caught;
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
        <div className="auth-shell-content">
          <Card className="login-card" bordered={false}>
            <Space direction="vertical" size="large" className="full-width">
              <div>
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
          <ComplianceFooter />
        </div>
      </main>
    );
  }

  const title =
    workspace === "imports"
      ? "数据采集"
      : workspace === "import-jobs"
        ? "导入记录"
        : workspace === "refresh-queues"
          ? refreshQueueId
            ? "数据更新名单"
            : "数据更新"
          : workspace === "outreach-today"
            ? "今日触达"
            : workspace === "campaigns"
              ? campaignId
                ? "拓客活动详情"
                : "拓客活动"
              : workspace === "candidate-pools"
                ? candidateRunId
                  ? "候选结果"
                  : candidatePoolId
                    ? "候选池详情"
                    : "候选池"
                : workspace === "buyer-prospects"
                  ? "潜在客户"
                : workspace === "permissions"
                  ? "权限管理"
                  : influencerId
                    ? "达人详情"
                    : "达人库";

  return (
    <AppShell
      title={title}
      description={
        workspace === "imports"
          ? "上传达人数据，完成文件检查后生成数据预览。"
          : undefined
      }
      department={auth.department.name}
      operator={auth.operator?.name ?? null}
      effectiveRole={
        auth.effective_role ? roleLabels[auth.effective_role] : null
      }
      showPermissionsNav={auth.effective_role === "super_admin"}
      onLogout={() => void handleLogout()}
      logoutLoading={submitting}
    >
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {requiresOperator ? null : workspace === "permissions" ? (
        auth.effective_role === "super_admin" ? (
          <PermissionsWorkspace onAuthRefresh={refreshAuth} />
        ) : (
          <Alert
            type="warning"
            showIcon
            message="当前身份无权访问权限管理"
            description={
              auth.operator
                ? "请使用拥有超级管理员有效角色的操作人。"
                : "请选择操作人后继续。"
            }
          />
        )
      ) : workspace === "influencers" ? (
        influencerId ? (
          <InfluencerDetailWorkspace influencerId={influencerId} />
        ) : (
          <InfluencerWorkspace />
        )
      ) : workspace === "import-jobs" ? (
        auth.effective_role ? (
          <ImportJobHistory role={auth.effective_role} />
        ) : null
      ) : workspace === "refresh-queues" ? (
        auth.effective_role ? (
          refreshQueueId ? (
            <RefreshQueueDetail
              queueId={refreshQueueId}
              role={auth.effective_role}
            />
          ) : (
            <RefreshQueueList
              role={auth.effective_role}
              departments={departments.filter(
                (department) => department.status === "active",
              )}
              currentDepartmentId={auth.department.id}
            />
          )
        ) : null
      ) : workspace === "outreach-today" ? (
        <TodayWorkspace />
      ) : workspace === "campaigns" ? (
        auth.effective_role ? (
          campaignId ? (
            <CampaignDetailView
              campaignId={campaignId}
              role={auth.effective_role}
              hasSelectedOperator={auth.operator !== null}
            />
          ) : (
            <CampaignListView
              role={auth.effective_role}
              hasSelectedOperator={auth.operator !== null}
            />
          )
        ) : null
      ) : workspace === "candidate-pools" ? (
        auth.effective_role ? (
          candidatePoolId ? (
            candidateRunId ? (
              <CandidateRunDetailView
                poolId={candidatePoolId}
                runId={candidateRunId}
                role={auth.effective_role}
                hasSelectedOperator={auth.operator !== null}
              />
            ) : (
              <CandidatePoolDetailView
                poolId={candidatePoolId}
                role={auth.effective_role}
                hasSelectedOperator={auth.operator !== null}
              />
            )
          ) : (
            <CandidatePoolListView
              role={auth.effective_role}
              hasSelectedOperator={auth.operator !== null}
            />
          )
        ) : null
      ) : workspace === "buyer-prospects" ? (
        auth.effective_role ? (
          <BuyerProspectWorkspace
            role={auth.effective_role}
            hasSelectedOperator={auth.operator !== null}
          />
        ) : null
      ) : auth.effective_role ? (
        <DataCollectionWorkspace role={auth.effective_role} />
      ) : null}

      <Modal
        title="选择当前操作人"
        open={requiresOperator && !auth.operator}
        footer={null}
        closable={false}
      >
        <Paragraph type="secondary">
          请选择本次操作的实际执行人，并输入该操作人的个人密码。
        </Paragraph>
        {error ? (
          <Alert
            type="error"
            showIcon
            message={error}
            className="modal-error"
          />
        ) : null}
        <Form<OperatorAuthValues>
          form={operatorForm}
          layout="vertical"
          onFinish={(values) => void handleOperatorSelect(values)}
        >
          <Form.Item
            label="操作人"
            name="operator_id"
            rules={[{ required: true, message: "请选择操作人" }]}
          >
            <Select
              size="large"
              placeholder="选择操作人"
              loading={submitting}
              options={operators.map((operator) => ({
                label: `${operator.name} · ${roleLabels[operator.role]}`,
                value: operator.id,
              }))}
            />
          </Form.Item>
          <Form.Item
            label="个人密码"
            name="operator_password"
            rules={[{ required: true, message: "请输入个人密码" }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              autoComplete="current-password"
              disabled={submitting}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            认证并继续
          </Button>
        </Form>
      </Modal>
    </AppShell>
  );
}
