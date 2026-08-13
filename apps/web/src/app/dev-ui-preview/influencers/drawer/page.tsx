import { PageHeader } from "@/components/ui/page-header";
import { notFound } from "next/navigation";

import { InfluencerPreviewShell } from "@/features/influencers/influencer-preview-shell";
import { createInfluencerPreviewDetailItems } from "@/features/influencers/preview-fixtures";

export default function DevInfluencerDrawerPreviewPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }

  const items = createInfluencerPreviewDetailItems();

  return (
    <InfluencerPreviewShell
      title="达人详情 Drawer 预览"
      description="仅用于界面预览"
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
    </InfluencerPreviewShell>
  );
}
