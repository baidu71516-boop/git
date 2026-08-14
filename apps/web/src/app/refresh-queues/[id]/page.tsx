import { AuthShell } from "@/components/auth-shell";

export default async function RefreshQueueDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <AuthShell workspace="refresh-queues" refreshQueueId={id} />;
}
