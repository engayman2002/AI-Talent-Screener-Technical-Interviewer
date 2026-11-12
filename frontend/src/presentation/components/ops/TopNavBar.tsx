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
    <header className="sticky top-0 z-50 border-b border-slate-800/80 bg-slate-950/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-5 py-3.5">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="flex items-center gap-2.5 text-sm font-bold tracking-tight text-slate-50 no-underline hover:text-sky-300"
          >
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-sky-500 to-emerald-500 text-xs font-extrabold text-slate-950 shadow-sm">
              AI
            </span>
            <span>AI Talent Screener &amp; Technical Interviewer</span>
          </Link>

          <span
            className={`hidden items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold sm:inline-flex ${
              isBackendOnline
                ? "border-emerald-500/35 bg-emerald-500/10 text-emerald-300"
                : "border-amber-500/35 bg-amber-500/10 text-amber-300"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                isBackendOnline ? "bg-emerald-400 animate-pulse" : "bg-amber-400"
              }`}
            />
            {isBackendOnline ? "ONLINE" : "FALLBACK"}
          </span>
        </div>

        <nav className="flex flex-wrap items-center gap-1.5" aria-label="Primary Navigation">
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold no-underline transition ${
                item.active
                  ? "border border-sky-500/40 bg-sky-500/15 text-sky-300"
                  : "border border-transparent text-slate-300 hover:border-slate-700 hover:bg-slate-900 hover:text-slate-100"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
