"use client";

import React, { useEffect, useRef, useState } from "react";
import { formatPercentage, formatShortUuid } from "../../../domain/shared/formatters";
import type {
  RecommendationBand,
  VerificationClassification,
} from "../../../domain/shared/types";
import { useComparisonMatrix } from "../../hooks/useComparisonMatrix";
import { useDemoBootstrap } from "../../hooks/useDemoBootstrap";

export interface ComparisonMatrixViewProps {
  readonly requisitionId: string;
}

const VERIFICATION_LABEL: Readonly<Record<VerificationClassification, string>> = {
  HIDDEN_GEM_UPGRADE: "HIDDEN GEM UPGRADE",
  CORROBORATED_CLAIM: "CORROBORATED",
  RESUME_INFLATION_WARNING: "INFLATION WARNING",
  CV_PRIOR_ONLY: "CV PRIOR ONLY",
};

const RECOMMENDATION_OPTIONS: readonly RecommendationBand[] = [
  "STRONG_HIRE",
  "HIRE",
  "BORDERLINE_HOLD",
  "NO_HIRE",
];

function bandBadgeClass(band: RecommendationBand): string {
  switch (band) {
    case "STRONG_HIRE":
      return "badge badge-success";
    case "HIRE":
      return "badge badge-info";
    case "BORDERLINE_HOLD":
      return "badge badge-warning";
    case "NO_HIRE":
      return "badge badge-danger";
  }
}

export function ComparisonMatrixView({
  requisitionId,
}: ComparisonMatrixViewProps): React.JSX.Element {
  const {
    state,
    loadFromBackend,
    toggleBlindMode,
    openEvidenceDrawer,
    submitOverride,
  } = useComparisonMatrix();
  const { state: demoState } = useDemoBootstrap();

  const [candidateIdsCsv, setCandidateIdsCsv] = useState("cand-001,cand-002,cand-003");
  const [selectedIds, setSelectedIds] = useState<readonly string[]>([]);
  const [overrideReportId, setOverrideReportId] = useState("");
  const [overrideBand, setOverrideBand] = useState<RecommendationBand>("HIRE");
  const [overrideJustification, setOverrideJustification] = useState("");
  const [uiNotice, setUiNotice] = useState<string | null>(null);
  const autoLoadedRef = useRef(false);

  useEffect(() => {
    const shortlisted = demoState.manifest.shortlisted_candidate_ids;
    if (demoState.isBackendOnline && !autoLoadedRef.current && shortlisted.length >= 2) {
      autoLoadedRef.current = true;
      setSelectedIds(shortlisted);
      setCandidateIdsCsv(shortlisted.join(","));
      if (demoState.manifest.report_ids.length > 0 && overrideReportId.trim() === "") {
        setOverrideReportId(demoState.manifest.report_ids[0] ?? "");
      }
      void loadFromBackend({
        requisitionId,
        candidateProfileIds: shortlisted,
        blindMode: state.blindMode,
      });
    }
  }, [
    demoState.isBackendOnline,
    demoState.manifest.report_ids,
    demoState.manifest.shortlisted_candidate_ids,
    loadFromBackend,
    overrideReportId,
    requisitionId,
    state.blindMode,
  ]);

  const nameByCandidateId: Readonly<Record<string, string>> = Object.fromEntries(
    demoState.manifest.candidate_summaries.map((c) => [c.candidate_profile_id, c.display_name]),
  );

  const toggleCandidateSelection = (candId: string): void => {
    setSelectedIds((prev) => {
      const next = prev.includes(candId)
        ? prev.filter((id) => id !== candId)
        : prev.length < 4
          ? [...prev, candId]
          : prev;
      setCandidateIdsCsv(next.join(","));
      return next;
    });
  };

  const handleLoadMatrix = async (ev: React.FormEvent): Promise<void> => {
    ev.preventDefault();
    setUiNotice(null);
    const ids =
      selectedIds.length >= 2
        ? selectedIds
        : candidateIdsCsv
            .split(",")
            .map((s) => s.trim())
            .filter((s) => s.length > 0);
    try {
      await loadFromBackend({
        requisitionId,
        candidateProfileIds: ids,
        blindMode: state.blindMode,
      });
    } catch (err) {
      setUiNotice(err instanceof Error ? err.message : "Failed to load comparison matrix.");
    }
  };

  const handleOverrideSubmit = async (ev: React.FormEvent): Promise<void> => {
    ev.preventDefault();
    setUiNotice(null);
    try {
      const targetBand = overrideBand;
      await submitOverride({
        reportId: overrideReportId.trim(),
        newBand: targetBand,
        justification: overrideJustification.trim(),
      });
      setUiNotice(`Recorded immutable recruiter override -> ${targetBand}.`);
      setOverrideJustification("");
    } catch (err) {
      setUiNotice(err instanceof Error ? err.message : "Failed to submit override.");
    }
  };

  const activeEvidenceCell = (() => {
    const sel = state.selectedEvidence;
    const mat = state.matrix;
    if (sel === null || mat === null) {
      return null;
    }
    const row = mat.rows.find((r) => r.dimension.competencyId === sel.competencyId);
    return row?.cellsByCandidateId[sel.candidateProfileId] ?? null;
  })();

  const reportableCandidates = demoState.manifest.candidate_summaries.filter(
    (c) => c.report_id !== null,
  );

  return (
    <div className="space-y-6">
      <header className="panel card-header-row">
        <div>
          <p className="eyebrow">
            Empirical Bayes Shrinkage Calibration &amp; Side-by-Side Finalist Matrix
          </p>
          <h1 className="page-title">Requisition {formatShortUuid(requisitionId)}</h1>
        </div>
        <div className="button-row">
          <button
            type="button"
            role="switch"
            aria-checked={state.blindMode}
            className={`btn ${state.blindMode ? "btn-primary" : "btn-secondary"}`}
            onClick={toggleBlindMode}
          >
            {state.blindMode ? "Blind Hiring Mode: ON" : "Blind Hiring Mode: OFF"}
          </button>
        </div>
      </header>

      <section className="panel" aria-label="Finalist Selection Bar">
        <form className="form-grid" onSubmit={(e) => void handleLoadMatrix(e)}>
          {reportableCandidates.length > 0 && (
            <div>
              <p className="eyebrow">Select 2 to 4 Finalists</p>
              <div className="mt-2 flex flex-wrap gap-3">
                {reportableCandidates.map((cand) => {
                  const checked = selectedIds.includes(cand.candidate_profile_id);
                  return (
                    <label
                      key={cand.candidate_profile_id}
                      className={`toggle-control rounded-lg border px-3 py-2 text-xs transition ${
                        checked
                          ? "border-sky-500/50 bg-sky-500/10 text-sky-200"
                          : "border-slate-800 bg-slate-950/70 text-slate-300"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleCandidateSelection(cand.candidate_profile_id)}
                      />
                      <span className="font-semibold">{cand.display_name}</span>
                      <span className="font-mono text-[11px] text-slate-400">
                        ({formatPercentage(cand.match_score)})
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 items-end gap-4 md:grid-cols-4">
            <label className="field-group md:col-span-3">
              <span>Candidate Profile IDs (2 to 4 comma-separated UUIDs)</span>
              <input
                type="text"
                value={candidateIdsCsv}
                onChange={(e) => setCandidateIdsCsv(e.target.value)}
              />
            </label>
            <button
              type="submit"
              className="btn btn-primary h-10"
              disabled={state.isLoading}
            >
              {state.isLoading ? "Loading Matrix..." : "Compare Finalists"}
            </button>
          </div>
        </form>

        {uiNotice !== null && (
          <p className="status-banner info" role="status">
            {uiNotice}
          </p>
        )}
        {state.problemError !== null && (
          <div className="status-banner danger" role="alert">
            <strong>
              {state.problemError.error_code} (HTTP {state.problemError.status}):
            </strong>{" "}
            {state.problemError.detail}
          </div>
        )}
      </section>

      {state.matrix !== null && (
        <section className="panel" aria-label="Normalized Comparison Table">
          <h2 className="section-title">
            Competency Posterior Alignment ({state.matrix.columns.length} Finalists)
          </h2>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Competency Dimension</th>
                  {state.matrix.columns.map((col) => {
                    const resolvedName = state.blindMode
                      ? col.displayName
                      : (nameByCandidateId[col.candidateProfileId] ?? col.displayName);
                    return (
                      <th key={col.candidateProfileId}>
                        <div className="flex flex-wrap items-center gap-2">
                          <strong className="text-slate-100">
                            #{col.rank} {resolvedName}
                          </strong>
                          <span className={bandBadgeClass(col.recommendationBand)}>
                            {col.recommendationBand}
                          </span>
                        </div>
                        <div className="mt-1 font-mono text-xs text-slate-400">
                          Index H = {col.holisticDecisionIndex.toFixed(1)} · Auth{" "}
                          {formatPercentage(col.authenticityBadge.score, 0)} (
                          {col.authenticityBadge.tier})
                        </div>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>
                    <strong className="text-slate-100">Composite Calibration Summary</strong>
                    <div className="muted">Weighted Interview &amp; CV Alignment</div>
                  </td>
                  {state.matrix.columns.map((col) => (
                    <td key={col.candidateProfileId}>
                      <div className="space-y-1 text-xs">
                        <div>
                          CV Match:{" "}
                          <strong className="font-mono text-sky-300">
                            {formatPercentage(col.compositeCvMatchScore)}
                          </strong>
                        </div>
                        <div>
                          Technical Posterior:{" "}
                          <strong className="font-mono text-slate-100">
                            {col.rawPosteriorTechnicalScore.toFixed(1)}
                          </strong>
                        </div>
                        <div>
                          Gap Resolution:{" "}
                          <strong className="font-mono text-emerald-300">
                            {formatPercentage(col.epistemicGapResolutionRatio, 0)}
                          </strong>
                        </div>
                      </div>
                    </td>
                  ))}
                </tr>
                {state.matrix.rows.map((row) => (
                  <tr key={row.dimension.competencyId}>
                    <td>
                      <strong className="text-slate-100">{row.dimension.competencyName}</strong>
                      <div className="muted mt-0.5 font-mono text-xs">
                        Weight {row.dimension.normalizedWeightPercent.toFixed(0)}% · Target L
                        {row.dimension.targetDepth}
                      </div>
                    </td>
                    {state.matrix?.columns.map((col) => {
                      const cell = row.cellsByCandidateId[col.candidateProfileId];
                      if (cell === undefined) {
                        return <td key={col.candidateProfileId}>—</td>;
                      }
                      const resolvedLabel = state.blindMode
                        ? col.displayName
                        : (nameByCandidateId[col.candidateProfileId] ?? col.displayName);
                      return (
                        <td key={col.candidateProfileId}>
                          <button
                            type="button"
                            className="cell-evidence-btn"
                            onClick={() =>
                              openEvidenceDrawer({
                                competencyId: row.dimension.competencyId,
                                competencyName: row.dimension.competencyName,
                                candidateProfileId: col.candidateProfileId,
                                candidateLabel: resolvedLabel,
                              })
                            }
                          >
                            <div>
                              <strong className="text-sm text-slate-100">
                                {cell.posteriorScore.toFixed(1)}
                              </strong>{" "}
                              <span className="muted font-mono text-xs">
                                (EB {cell.empiricalBayesScore.toFixed(1)})
                              </span>
                            </div>
                            <div className="muted font-mono text-xs">
                              Depth {cell.achievedDepth.toFixed(1)} / L{cell.targetDepth} · Peak L
                              {cell.peakDifficultyReached}
                            </div>
                            <div className="badge-row">
                              {cell.isDimensionLeader && (
                                <span className="badge badge-success">DIMENSION_LEADER</span>
                              )}
                              <span className="badge badge-neutral">
                                {VERIFICATION_LABEL[cell.verificationClassification]}
                              </span>
                            </div>
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {state.selectedEvidence !== null && (
        <aside className="panel drawer-panel" aria-label="Evidence Drilldown Drawer">
          <div className="section-row mb-4">
            <h2 className="section-title mb-0">
              Evidence Drilldown: {state.selectedEvidence.candidateLabel} —{" "}
              {state.selectedEvidence.competencyName}
            </h2>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => openEvidenceDrawer(null)}
            >
              Close Drawer
            </button>
          </div>
          {activeEvidenceCell !== null ? (
            <div className="evidence-grid">
              <div>
                <span className="eyebrow">Grounded CV Claim Quote</span>
                <p className="text-sm text-slate-200">{activeEvidenceCell.cvEvidenceQuote}</p>
              </div>
              <div>
                <span className="eyebrow">Adaptive Probe Question</span>
                <p className="text-sm text-slate-200">{activeEvidenceCell.interviewQuestion}</p>
              </div>
              <div>
                <span className="eyebrow">Candidate Response Excerpt</span>
                <p className="text-sm text-slate-200">
                  {activeEvidenceCell.candidateAnswerExcerpt}
                </p>
              </div>
              <div>
                <span className="eyebrow">Evaluator Rationale</span>
                <p className="text-sm text-slate-200">{activeEvidenceCell.evaluatorRationale}</p>
              </div>
            </div>
          ) : (
            <p className="muted">No detailed evidence loaded for this cell.</p>
          )}
        </aside>
      )}

      <section className="panel" aria-label="Recruiter Recommendation Override">
        <p className="eyebrow">Governance &amp; Audit Ledger</p>
        <h2 className="section-title">Auditable Recruiter Override</h2>
        <form className="form-grid" onSubmit={(e) => void handleOverrideSubmit(e)}>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <label className="field-group">
              <span>Evaluation Report ID</span>
              {reportableCandidates.length > 0 ? (
                <select
                  value={overrideReportId}
                  onChange={(e) => setOverrideReportId(e.target.value)}
                >
                  {reportableCandidates.map((cand) => (
                    <option key={cand.report_id ?? cand.candidate_profile_id} value={cand.report_id ?? ""}>
                      {cand.display_name} — {cand.recommendation_band} ({formatShortUuid(cand.report_id ?? "")})
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={overrideReportId}
                  onChange={(e) => setOverrideReportId(e.target.value)}
                  placeholder="rep-..."
                />
              )}
            </label>
            <label className="field-group">
              <span>Override Recommendation Band</span>
              <select
                value={overrideBand}
                onChange={(e) => setOverrideBand(e.target.value as RecommendationBand)}
              >
                {RECOMMENDATION_OPTIONS.map((band) => (
                  <option key={band} value={band}>
                    {band}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="field-group">
            <span>Audit Justification (Mandatory)</span>
            <textarea
              rows={3}
              value={overrideJustification}
              onChange={(e) => setOverrideJustification(e.target.value)}
              placeholder="Document committee consensus and specific technical evidence justifying this band change..."
            />
          </label>
          <div className="button-row">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={overrideReportId.trim().length === 0 || overrideJustification.trim().length < 5}
            >
              Submit Audited Override
            </button>
          </div>
        </form>

        {state.lastOverrideReceipt !== null && (
          <div className="status-banner success">
            Override saved: Report <code>{formatShortUuid(state.lastOverrideReceipt.report_id)}</code>{" "}
            effective band is now <strong>{state.lastOverrideReceipt.effective_recommendation_band}</strong>{" "}
            (Override count: {state.lastOverrideReceipt.override_count}).
          </div>
        )}
      </section>
    </div>
  );
}
