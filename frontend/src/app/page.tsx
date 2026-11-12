import Link from "next/link";
import React from "react";

export default function HomePage(): React.JSX.Element {
  return (
    <div className="panel-stack">
      <section className="panel">
        <p className="eyebrow">Phase 7 Frontend Clean Architecture Workspace</p>
        <h1 className="page-title">
          AI Talent Screener &amp; Adaptive Technical Interviewer
        </h1>
        <p className="muted">
          Four-layer Frontend Clean Architecture (Domain, Application,
          Infrastructure, Presentation) aligned with the FastAPI &amp; LangGraph
          backend contracts.
        </p>

        <div className="card-grid">
          <article className="panel">
            <p className="eyebrow">Candidate Portal</p>
            <h2 className="section-title">13-State Adaptive Interview</h2>
            <p className="muted">
              Full-duplex WebSocket streaming, Web Audio ring-buffer barge-in
              offset synchronization, accommodated text mode, and non-intrusive
              kinematic telemetry.
            </p>
            <Link href="/interview/sess-demo-01" className="btn btn-primary">
              Open Interview Portal
            </Link>
          </article>

          <article className="panel">
            <p className="eyebrow">Recruiter Pipeline</p>
            <h2 className="section-title">500-CV Batch Monitor</h2>
            <p className="muted">
              Chunked SHA-256 manifest uploader and 60 FPS requestAnimationFrame
              coalesced Server-Sent Events progress tracker.
            </p>
            <Link
              href="/recruiter/batches/batch-demo-01"
              className="btn btn-primary"
            >
              Open Batch Monitor
            </Link>
          </article>

          <article className="panel">
            <p className="eyebrow">Hiring Decision Cockpit</p>
            <h2 className="section-title">Candidate Comparison Matrix</h2>
            <p className="muted">
              Empirical Bayes shrinkage normalization, Blind Hiring mode toggle,
              grounded evidence drilldown drawer, and immutable recommendation
              overrides.
            </p>
            <Link
              href="/recruiter/requisitions/req-staff-backend-01/compare"
              className="btn btn-primary"
            >
              Open Comparison Matrix
            </Link>
          </article>
        </div>
      </section>
    </div>
  );
}
