"use client";

import { useSyncExternalStore } from "react";
import type { StartInterviewSessionCommand } from "../../application/interview/session_coordinator";
import type { InterviewPortalSnapshot } from "../../domain/interview/fsm";
import { useFrontendServices } from "../providers/AppServicesProvider";

export interface InterviewControllerHook {
  readonly snapshot: InterviewPortalSnapshot;
  readonly jitterCushionMs: number;
  startSession(cmd: StartInterviewSessionCommand): Promise<void>;
  triggerBargeIn(): Promise<number>;
  markTurnInputReady(): void;
  updateSupplementalText(text: string, source?: "TYPED" | "PASTED"): void;
  submitUtterance(overrideText?: string): Promise<void>;
  setAccommodatedTextMode(enabled: boolean): void;
  terminateSession(reason: string): Promise<void>;
}

export function useInterviewController(): InterviewControllerHook {
  const { interviewCoordinator } = useFrontendServices();
  const snapshot = useSyncExternalStore(
    interviewCoordinator.subscribe,
    interviewCoordinator.getSnapshot,
    interviewCoordinator.getSnapshot,
  );

  return {
    snapshot,
    jitterCushionMs: interviewCoordinator.getJitterCushionMs(),
    startSession: (cmd) => interviewCoordinator.startSession(cmd),
    triggerBargeIn: () => interviewCoordinator.triggerBargeIn(),
    markTurnInputReady: () => interviewCoordinator.markTurnInputReady(),
    updateSupplementalText: (text, source) =>
      interviewCoordinator.updateSupplementalText(text, source),
    submitUtterance: (overrideText) => interviewCoordinator.submitUtterance(overrideText),
    setAccommodatedTextMode: (enabled) => interviewCoordinator.setAccommodatedTextMode(enabled),
    terminateSession: (reason) => interviewCoordinator.terminateSession(reason),
  };
}
