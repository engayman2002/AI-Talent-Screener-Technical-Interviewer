import React from "react";
import { ComparisonMatrixView } from "../../../../../presentation/components/evaluation/ComparisonMatrixView";

export interface RequisitionComparisonPageProps {
  readonly params: Promise<{ readonly requisitionId: string }>;
}

export default async function RequisitionComparisonPage({
  params,
}: RequisitionComparisonPageProps): Promise<React.JSX.Element> {
  const { requisitionId } = await params;
  return <ComparisonMatrixView requisitionId={requisitionId} />;
}
