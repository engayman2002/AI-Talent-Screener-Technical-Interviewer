"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import React from "react";
import { useDemoBootstrap } from "../../hooks/useDemoBootstrap";

export function TopNavBar(): React.JSX.Element {
  const pathname = usePathname();
  const { state } = useDemoBootstrap();
  const { manifest, isBackendOnline } = state;

  const navItems = [
    {
      href: "/",
      label: "Command Center",
      active: pathname === "/",
    },
    {
      href: `/interview/${manifest.active_session_id}`,
      label: "Candidate Interview",
      active: pathname.startsWith("/interview"),
    },
    {
      href: `/recruiter/batches/${manifest.batch_id}`,
      label: "Batch Monitor",
      active: pathname.startsWith("/recruiter/batches"),
    },
    {
      href: `/recruiter/requisitions/${manifest.requisition_id}/compare`,
      label: "Comparison Matrix",
      active: pathname.includes("/compare"),
    },
  ];

  return (
    <header className="nav-header">
      <div className="nav-inner">
        <div className="nav-brand-group">
          <Link href="/" className="nav-brand-link">
            <span className="nav-logo-badge">AI</span>
            <span className="nav-brand-title">AI Talent Screener &amp; Technical Interviewer</span>
          </Link>

          <span
            className={`nav-status-pill ${
              isBackendOnline ? "nav-status-pill--online" : "nav-status-pill--offline"
            }`}
          >
            <span
              className={`nav-status-dot ${
                isBackendOnline ? "nav-status-dot--online" : "nav-status-dot--offline"
              }`}
            />
            {isBackendOnline ? "ONLINE" : "FALLBACK"}
          </span>
        </div>

        <nav className="nav-tabs" aria-label="Primary Navigation">
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`nav-tab ${item.active ? "nav-tab--active" : ""}`}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
