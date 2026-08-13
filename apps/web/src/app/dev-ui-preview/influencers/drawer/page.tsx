import { notFound } from "next/navigation";

import { DevInfluencerDrawerPreviewPageContent } from "./dev-drawer-preview-page-content";

export default function DevInfluencerDrawerPreviewPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }

  return <DevInfluencerDrawerPreviewPageContent />;
}
