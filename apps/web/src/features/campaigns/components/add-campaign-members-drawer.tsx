"use client";

import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Empty,
  Input,
  Modal,
  Radio,
  Space,
  Spin,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import { platformLabel } from "@/features/influencers/formatters";
import { useInfluencerList } from "@/features/influencers/queries";
import type {
  InfluencerListItem,
  PlatformAccountSummary,
} from "@/features/influencers/types";

import type { AddCampaignMembersPreview } from "../preview-types";
import type { CampaignMemberAddItem } from "../types";

const { Text } = Typography;
const DRAWER_PAGE_SIZE = 20;

type SelectedInfluencer = {
  influencer: InfluencerListItem;
  preferredPlatformAccountId: string | null;
};

function activeAccounts(item: InfluencerListItem): PlatformAccountSummary[] {
  return item.platform_accounts.filter((account) => account.is_active);
}

function accountLabel(account: PlatformAccountSummary): string {
  const handle = account.account_handle ? ` · @${account.account_handle}` : "";
  return `${platformLabel(account.platform)} · ${account.account_name}${handle}`;
}

function createAttemptKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

type AddCampaignMembersDrawerProps = {
  open: boolean;
  submitting: boolean;
  submitError: string | null;
  onClose: () => void;
  onSubmit?: (
    input: CampaignMemberAddItem[],
    idempotencyKey: string,
  ) => Promise<"retry" | "new">;
  preview?: AddCampaignMembersPreview;
};

function RemoteAddCampaignMembersDrawer({
  open,
  submitting,
  submitError,
  onClose,
  onSubmit,
}: {
  open: boolean;
  submitting: boolean;
  submitError: string | null;
  onClose: () => void;
  onSubmit: (
    input: CampaignMemberAddItem[],
    idempotencyKey: string,
  ) => Promise<"retry" | "new">;
}) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selectedOnly, setSelectedOnly] = useState(false);
  const [selection, setSelection] = useState<
    Record<string, SelectedInfluencer>
  >({});
  const [discardConfirmOpen, setDiscardConfirmOpen] = useState(false);
  const [attempt, setAttempt] = useState<{
    key: string;
    payload: string;
  } | null>(null);
  const candidatesQuery = useInfluencerList(
    {
      q: search || undefined,
      page: String(page),
      page_size: String(DRAWER_PAGE_SIZE),
    },
    open && !selectedOnly,
  );
  const selected = useMemo(() => Object.values(selection), [selection]);
  const canSubmit =
    selected.length > 0 &&
    selected.every((item) => item.preferredPlatformAccountId);

  function invalidateAttempt() {
    setAttempt(null);
  }

  function toggle(item: InfluencerListItem, checked: boolean) {
    invalidateAttempt();
    setSelection((current) => {
      if (!checked) {
        const rest = { ...current };
        delete rest[item.id];
        return rest;
      }
      const accounts = activeAccounts(item);
      return {
        ...current,
        [item.id]: {
          influencer: item,
          preferredPlatformAccountId:
            accounts.length === 1 ? accounts[0].id : null,
        },
      };
    });
  }

  function changeAccount(influencerId: string, accountId: string) {
    invalidateAttempt();
    setSelection((current) => {
      const entry = current[influencerId];
      return entry
        ? {
            ...current,
            [influencerId]: { ...entry, preferredPlatformAccountId: accountId },
          }
        : current;
    });
  }

  function requestClose() {
    if (submitting) return;
    if (selected.length > 0) {
      setDiscardConfirmOpen(true);
      return;
    }
    onClose();
  }

  async function submit() {
    if (!canSubmit) return;
    const input = selected.map((entry) => ({
      influencer_id: entry.influencer.id,
      preferred_platform_account_id: entry.preferredPlatformAccountId as string,
    }));
    const payload = JSON.stringify(input);
    const currentAttempt = attempt?.payload === payload ? attempt : null;
    const idempotencyKey = currentAttempt?.key ?? createAttemptKey();
    if (!currentAttempt) setAttempt({ key: idempotencyKey, payload });
    const nextAttempt = await onSubmit(input, idempotencyKey);
    if (nextAttempt === "new") setAttempt(null);
  }

  const displayedItems = selectedOnly
    ? selected.map((entry) => entry.influencer)
    : (candidatesQuery.data?.items ?? []);
  const totalPages = candidatesQuery.data
    ? Math.max(
        1,
        Math.ceil(candidatesQuery.data.total / candidatesQuery.data.page_size),
      )
    : 1;

  return (
    <>
      <Drawer
        open={open}
        size="large"
        className="campaign-member-add-drawer"
        rootClassName="campaign-member-add-drawer-root"
        title={
          <div>
            <div>添加达人</div>
            <Text type="secondary">选择达人，并指定该活动使用的平台账号。</Text>
          </div>
        }
        closable={{ "aria-label": "关闭添加达人" }}
        mask={{ closable: !submitting }}
        keyboard={!submitting}
        onClose={requestClose}
        footer={
          <div className="campaign-member-drawer-footer">
            <Text type="secondary">已选择 {selected.length} 位达人</Text>
            <Space>
              <Button onClick={requestClose} disabled={submitting}>
                取消
              </Button>
              <Button
                type="primary"
                onClick={() => void submit()}
                loading={submitting}
                disabled={!canSubmit}
              >
                添加到活动
              </Button>
            </Space>
          </div>
        }
      >
        <div className="campaign-member-drawer-content">
          {submitError ? (
            <Alert type="error" showIcon message={submitError} />
          ) : null}
          <div className="campaign-member-drawer-toolbar">
            <Input
              value={search}
              disabled={selectedOnly}
              placeholder="搜索达人昵称或平台账号名称"
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(1);
              }}
            />
            <Button
              type="link"
              onClick={() => setSelectedOnly((value) => !value)}
            >
              {selectedOnly ? "返回搜索结果" : "查看已选"}
            </Button>
          </div>
          {selectedOnly ? null : candidatesQuery.isPending ? (
            <div className="campaign-member-drawer-loading">
              <Spin />
            </div>
          ) : candidatesQuery.isError ? (
            <Alert
              type="error"
              showIcon
              message="达人列表加载失败"
              description="请稍后重试。"
              action={
                <Button onClick={() => void candidatesQuery.refetch()}>
                  重新加载
                </Button>
              }
            />
          ) : null}
          {!selectedOnly &&
          !candidatesQuery.isPending &&
          !candidatesQuery.isError &&
          displayedItems.length === 0 ? (
            <Empty description="暂无匹配达人" />
          ) : (
            <div className="campaign-member-candidate-list">
              {displayedItems.map((item) => {
                const entry = selection[item.id];
                const accounts = activeAccounts(item);
                return (
                  <div className="campaign-member-candidate" key={item.id}>
                    <Checkbox
                      checked={Boolean(entry)}
                      onChange={(event) => toggle(item, event.target.checked)}
                    >
                      {item.display_name}
                    </Checkbox>
                    {entry ? (
                      accounts.length === 0 ? (
                        <Text type="secondary">暂无可用平台账号</Text>
                      ) : accounts.length === 1 ? (
                        <Text type="secondary">
                          {accountLabel(accounts[0])}
                        </Text>
                      ) : (
                        <Radio.Group
                          value={entry.preferredPlatformAccountId}
                          onChange={(event) =>
                            changeAccount(item.id, event.target.value)
                          }
                        >
                          <Space orientation="vertical">
                            {accounts.map((account) => (
                              <Radio key={account.id} value={account.id}>
                                {accountLabel(account)}
                              </Radio>
                            ))}
                          </Space>
                        </Radio.Group>
                      )
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
          {!selectedOnly && candidatesQuery.data && totalPages > 1 ? (
            <div className="campaign-member-drawer-pagination">
              <Button
                disabled={page <= 1}
                onClick={() => setPage((value) => value - 1)}
              >
                上一页
              </Button>
              <Text type="secondary">
                第 {page} / {totalPages} 页
              </Text>
              <Button
                disabled={page >= totalPages}
                onClick={() => setPage((value) => value + 1)}
              >
                下一页
              </Button>
            </div>
          ) : null}
        </div>
      </Drawer>
      <Modal
        open={discardConfirmOpen}
        title="放弃本次选择？"
        okText="放弃并关闭"
        cancelText="继续选择"
        okButtonProps={{ danger: true }}
        onCancel={() => setDiscardConfirmOpen(false)}
        onOk={() => {
          setDiscardConfirmOpen(false);
          onClose();
        }}
      >
        <Text>关闭后，尚未添加的达人选择将不会保留。</Text>
      </Modal>
    </>
  );
}

function PreviewAddCampaignMembersDrawer({
  open,
  submitting,
  submitError,
  onClose,
  preview,
}: {
  open: boolean;
  submitting: boolean;
  submitError: string | null;
  onClose: () => void;
  preview: AddCampaignMembersPreview;
}) {
  const initialSelection = useMemo(() => {
    const selectedIds = new Set(preview.initialSelectedIds ?? []);
    return Object.fromEntries(
      preview.candidates
        .filter((item) => selectedIds.has(item.id))
        .map((item) => {
          const accounts = activeAccounts(item);
          return [
            item.id,
            {
              influencer: item,
              preferredPlatformAccountId:
                accounts.length === 1 ? accounts[0]!.id : null,
            } satisfies SelectedInfluencer,
          ];
        }),
    ) as Record<string, SelectedInfluencer>;
  }, [preview.candidates, preview.initialSelectedIds]);
  const [search, setSearch] = useState("");
  const [selectedOnly, setSelectedOnly] = useState(
    preview.initialSelectedOnly ?? false,
  );
  const [selection, setSelection] =
    useState<Record<string, SelectedInfluencer>>(initialSelection);
  const [discardConfirmOpen, setDiscardConfirmOpen] = useState(false);
  const [previewSubmitting, setPreviewSubmitting] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const selected = useMemo(() => Object.values(selection), [selection]);
  const displayedItems = selectedOnly
    ? selected.map((entry) => entry.influencer)
    : preview.candidates.filter((item) => {
        if (!search.trim()) return true;
        const query = search.trim().toLowerCase();
        return (
          item.display_name.toLowerCase().includes(query) ||
          item.platform_accounts.some((account) =>
            account.account_name.toLowerCase().includes(query),
          )
        );
      });
  const canSubmit =
    selected.length > 0 &&
    selected.every((item) => item.preferredPlatformAccountId);

  function toggle(item: InfluencerListItem, checked: boolean) {
    setPreviewError(null);
    setSuccess(false);
    setSelection((current) => {
      if (!checked) {
        const next = { ...current };
        delete next[item.id];
        return next;
      }
      const accounts = activeAccounts(item);
      return {
        ...current,
        [item.id]: {
          influencer: item,
          preferredPlatformAccountId:
            accounts.length === 1 ? accounts[0]!.id : null,
        },
      };
    });
  }

  function changeAccount(influencerId: string, accountId: string) {
    setPreviewError(null);
    setSuccess(false);
    setSelection((current) => {
      const entry = current[influencerId];
      return entry
        ? {
            ...current,
            [influencerId]: { ...entry, preferredPlatformAccountId: accountId },
          }
        : current;
    });
  }

  function requestClose() {
    if (submitting || previewSubmitting) return;
    if (selected.length > 0) {
      setDiscardConfirmOpen(true);
      return;
    }
    onClose();
  }

  function submit() {
    if (!canSubmit) return;
    const input = selected.map((entry) => ({
      influencer_id: entry.influencer.id,
      preferred_platform_account_id: entry.preferredPlatformAccountId as string,
    }));
    setPreviewError(null);
    setSuccess(false);
    setPreviewSubmitting(true);
    window.setTimeout(() => {
      setPreviewSubmitting(false);
      if (preview.submitOutcome === "account-error") {
        setPreviewError(
          "所选平台账号已不可用或与达人不匹配。请重新选择后再提交。",
        );
        return;
      }
      preview.onSubmit?.(input);
      setSuccess(true);
    }, 260);
  }

  return (
    <>
      <Drawer
        open={open}
        size="large"
        className="campaign-member-add-drawer"
        rootClassName="campaign-member-add-drawer-root"
        title={
          <div>
            <div>添加达人</div>
            <Text type="secondary">选择达人，并指定该活动使用的平台账号。</Text>
          </div>
        }
        closable={{ "aria-label": "关闭添加达人" }}
        mask={{ closable: !submitting && !previewSubmitting }}
        keyboard={!submitting && !previewSubmitting}
        onClose={requestClose}
        footer={
          <div className="campaign-member-drawer-footer">
            <Text type="secondary">已选择 {selected.length} 位达人</Text>
            <Space>
              <Button
                onClick={requestClose}
                disabled={submitting || previewSubmitting}
              >
                取消
              </Button>
              <Button
                type="primary"
                onClick={submit}
                loading={submitting || previewSubmitting}
                disabled={!canSubmit || previewSubmitting}
              >
                添加到活动
              </Button>
            </Space>
          </div>
        }
      >
        <div className="campaign-member-drawer-content">
          {previewError || submitError ? (
            <Alert
              type="error"
              showIcon
              message={previewError ?? submitError}
            />
          ) : null}
          {success ? (
            <Alert
              type="success"
              showIcon
              message="达人添加完成"
              description="新增 8 · 重新加入 1 · 已在活动中 2。已在活动中的达人未做修改。"
            />
          ) : null}
          <div className="campaign-member-drawer-toolbar">
            <Input
              value={search}
              disabled={selectedOnly}
              placeholder="搜索达人昵称或平台账号名称"
              onChange={(event) => setSearch(event.target.value)}
            />
            <Button
              type="link"
              onClick={() => setSelectedOnly((value) => !value)}
            >
              {selectedOnly ? "返回搜索结果" : "查看已选"}
            </Button>
          </div>
          <div className="campaign-member-candidate-list">
            {displayedItems.map((item) => {
              const entry = selection[item.id];
              const accounts = activeAccounts(item);
              const hasNoAccounts = accounts.length === 0;
              return (
                <div className="campaign-member-candidate" key={item.id}>
                  <Checkbox
                    checked={Boolean(entry)}
                    disabled={hasNoAccounts}
                    onChange={(event) => toggle(item, event.target.checked)}
                  >
                    {item.display_name}
                  </Checkbox>
                  {hasNoAccounts ? (
                    <Text type="secondary">暂无可用平台账号</Text>
                  ) : entry && accounts.length === 1 ? (
                    <div className="campaign-member-account">
                      <div>
                        活动账号 · {platformLabel(accounts[0]!.platform)} ·{" "}
                        {accounts[0]!.account_name}
                      </div>
                      {accounts[0]!.account_handle ? (
                        <Text type="secondary">
                          @{accounts[0]!.account_handle}
                        </Text>
                      ) : null}
                    </div>
                  ) : entry ? (
                    <Radio.Group
                      value={entry.preferredPlatformAccountId}
                      onChange={(event) =>
                        changeAccount(item.id, event.target.value)
                      }
                    >
                      <Space orientation="vertical">
                        <Text type="secondary">活动账号</Text>
                        {accounts.map((account) => (
                          <Radio key={account.id} value={account.id}>
                            <span className="campaign-member-account-option">
                              <span>
                                {platformLabel(account.platform)} ·{" "}
                                {account.account_name}
                              </span>
                              {account.account_handle ? (
                                <Text type="secondary">
                                  @{account.account_handle}
                                </Text>
                              ) : null}
                            </span>
                          </Radio>
                        ))}
                      </Space>
                    </Radio.Group>
                  ) : null}
                </div>
              );
            })}
          </div>
          {selected.length > 0 && !canSubmit ? (
            <Text type="secondary">请为已选择的达人指定活动账号后再提交。</Text>
          ) : null}
        </div>
      </Drawer>
      <Modal
        open={discardConfirmOpen}
        title="放弃本次选择？"
        okText="放弃并关闭"
        cancelText="继续选择"
        onCancel={() => setDiscardConfirmOpen(false)}
        onOk={() => {
          setDiscardConfirmOpen(false);
          onClose();
        }}
      >
        <Text>关闭后，尚未添加的达人选择将不会保留。</Text>
      </Modal>
    </>
  );
}

export function AddCampaignMembersDrawer(props: AddCampaignMembersDrawerProps) {
  if (props.preview) {
    return (
      <PreviewAddCampaignMembersDrawer {...props} preview={props.preview} />
    );
  }
  if (!props.onSubmit) return null;
  return (
    <RemoteAddCampaignMembersDrawer {...props} onSubmit={props.onSubmit} />
  );
}
