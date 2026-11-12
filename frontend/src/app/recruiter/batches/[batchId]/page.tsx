import React from "react";
import { BatchMonitorView } from "../../../../presentation/components/screening/BatchMonitorView";

export interface BatchMonitorPageProps {
  readonly params: Promise<{ readonly batchId: string }>;
}

export default async function BatchMonitorPage({
  params,
}: BatchMonitorPageProps): Promise<React.JSX.Element> {
  const { batchId } = await params;
  return <BatchMonitorView batchId={batchId} />;
}
