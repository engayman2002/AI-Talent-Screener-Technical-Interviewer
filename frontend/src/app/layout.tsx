import type { Metadata } from "next";
import React from "react";
import { TopNavBar } from "../presentation/components/ops/TopNavBar";
import { AppServicesProvider } from "../presentation/providers/AppServicesProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Talent Screener & Technical Interviewer",
  description:
    "Clean Architecture Next.js Portal for Real-Time Adaptive Interviews, 500-CV Screening Batches, and Bayesian Candidate Comparison",
};

export interface RootLayoutProps {
  readonly children: React.ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps): React.JSX.Element {
  return (
    <html lang="en">
      <body className="app-body">
        <AppServicesProvider>
          <TopNavBar />
          <main className="app-shell-main">{children}</main>
        </AppServicesProvider>
      </body>
    </html>
  );
}
