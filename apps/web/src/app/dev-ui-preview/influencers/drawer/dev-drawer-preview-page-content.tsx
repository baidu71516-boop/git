"use client";

import Link from "next/link";

import { PageHeader } from "@/components/ui/page-header";
import { InfluencerAvatar } from "@/features/influencers/components/influencer-avatar";

import { InfluencerPreviewShell } from "@/features/influencers/influencer-preview-shell";
import { createInfluencerPreviewDetailItems } from "@/features/influencers/preview-fixtures";
import {
  crmStageDisplay,
  platformLabel,
} from "@/features/influencers/formatters";

export function DevInfluencerDrawerPreviewPageContent() {
  const items = createInfluencerPreviewDetailItems();

  return (
    <InfluencerPreviewShell title="达人详情预览" description="仅开发环境可见">
      <section className="influencer-workspace influencer-detail-preview-list-workspace">
        <div className="influencer-page-heading">
          <PageHeader title="达人详情预览" description="仅开发环境可见" />
        </div>
        <div className="detail-secondary-text dev-preview-drawer-subtitle">
          点击任意达人卡片进入预览详情
        </div>
        <div className="dev-preview-drawer-grid">
          {items.map((item) => (
            <Link
              key={item.id}
              className="dev-preview-drawer-item"
              href={`/dev-ui-preview/influencers/drawer/${item.id}`}
            >
              <InfluencerAvatar
                size={48}
                name={item.display_name}
                avatarUrl={item.avatar_url ?? null}
                square={false}
              />
              <div className="dev-preview-drawer-item-content">
                <h3>{item.display_name}</h3>
                <div className="detail-secondary-text">
                  {crmStageDisplay(item.crm_stage).label} ·{" "}
                  {platformLabel(
                    item.platform_accounts[0]?.platform ?? "xiaohongshu",
                  )}
                </div>
              </div>
            </Link>
          ))}
        </div>
      </section>
    </InfluencerPreviewShell>
  );
}
