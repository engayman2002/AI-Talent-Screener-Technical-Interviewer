import React from "react";
import { InterviewPortalView } from "../../../presentation/components/interview/InterviewPortalView";

export interface InterviewSessionPageProps {
  readonly params: Promise<{ readonly sessionId: string }>;
}

export default async function InterviewSessionPage({
  params,
}: InterviewSessionPageProps): Promise<React.JSX.Element> {
  const { sessionId } = await params;
  return <InterviewPortalView sessionId={sessionId} />;
}
