import type { Metadata } from "next";
import Link from "next/link";
import React from "react";
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
      <body>
        <AppServicesProvider>
          <header className="top-nav">
            <Link href="/" className="top-nav-brand">
              AI Talent Screener &amp; Technical Interviewer
            </Link>
            <nav className="top-nav-links" aria-label="Primary Navigation">
              <Link href="/interview/sess-demo-01">Candidate Interview</Link>
              <Link href="/recruiter/batches/batch-demo-01">Batch Monitor</Link>
              <Link href="/recruiter/requisitions/req-staff-backend-01/compare">
                Comparison Matrix
              </Link>
            </nav>
          </header>
          <main className="app-main">{children}</main>
        </AppServicesProvider>
      </body>
    </html>
  );
}
