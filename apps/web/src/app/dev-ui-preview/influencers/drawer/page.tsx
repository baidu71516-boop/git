import { AppShell } from "@/components/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { notFound } from "next/navigation";

import { createInfluencerPreviewDetailItems } from "@/features/influencers/preview-fixtures";

export default function DevInfluencerDrawerPreviewPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }

  const items = createInfluencerPreviewDetailItems();

  return (
    <AppShell
      title="达人详情 Drawer 预览"
      department="界面预览"
      operator="预览用户"
      role="仅展示"
      onLogout={() => undefined}
    >
      <section className="influencer-workspace influencer-detail-preview-list-workspace">
        <div className="influencer-page-heading">
          <PageHeader
            title="达人详情 Drawer 预览"
            description="仅用于界面预览 · Development only"
          />
        </div>
        <ul className="dev-preview-drawer-list">
          {items.map((item) => (
            <li className="dev-preview-drawer-item" key={item.id}>
              <a href={`/dev-ui-preview/influencers/drawer/${item.id}`}>
                {item.display_name}
              </a>
            </li>
          ))}
        </ul>
      </section>
    </AppShell>
  );
}
