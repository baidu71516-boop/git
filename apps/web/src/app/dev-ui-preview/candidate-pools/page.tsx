import { notFound } from "next/navigation";

import { CandidatePoolPreviewWorkspace } from "@/features/candidate-pools/candidate-pool-preview-workspace";

export default function CandidatePoolVisualPreviewPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }
  return <CandidatePoolPreviewWorkspace />;
}
