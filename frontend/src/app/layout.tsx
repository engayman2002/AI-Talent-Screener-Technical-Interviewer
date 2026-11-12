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
    <html lang="en" className="dark">
      <body className="min-h-screen bg-slate-950 text-slate-50 antialiased">
        <AppServicesProvider>
          <TopNavBar />
          <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</main>
        </AppServicesProvider>
      </body>
    </html>
  );
}
