"use client";

import React, { useEffect, useRef, useState } from "react";
import type { RecommendationBand, VerificationClassification } from "../../../domain/shared/types";
import { useDemoBootstrap } from "../../hooks/useDemoBootstrap";
import { useComparisonMatrix } from "../../hooks/useComparisonMatrix";

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

  const [candidateIdsCsv, setCandidateIdsCsv] = useState(
    "cand-001,cand-002,cand-003",
  );
  const [overrideReportId, setOverrideReportId] = useState("");
  const [overrideBand, setOverrideBand] = useState<RecommendationBand>("HIRE");
  const [overrideJustification, setOverrideJustification] = useState("");
  const [uiNotice, setUiNotice] = useState<string | null>(null);
  const autoLoadedRef = useRef(false);

  useEffect(() => {
    const shortlisted = demoState.manifest.shortlisted_candidate_ids;
    if (
      demoState.isBackendOnline &&
      !autoLoadedRef.current &&
      shortlisted.length >= 2
    ) {
      autoLoadedRef.current = true;
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

  const handleLoadMatrix = async (ev: React.FormEvent): Promise<void> => {
    ev.preventDefault();
    setUiNotice(null);
    const ids = candidateIdsCsv
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
      setUiNotice(
        err instanceof Error ? err.message : "Failed to load comparison matrix.",
      );
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
      setUiNotice(
        err instanceof Error ? err.message : "Failed to submit override.",
      );
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

  return (
    <div className="panel-stack">
      <header className="panel card-header-row">
        <div>
          <p className="eyebrow">
            Bayesian Shrinkage Calibration & Side-by-Side Finalist Matrix
          </p>
          <h1 className="page-title">Requisition {requisitionId}</h1>
        </div>
        <div className="button-row">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={toggleBlindMode}
          >
            {state.blindMode ? "Disable Blind Hiring Mode" : "Enable Blind Hiring Mode"}
          </button>
        </div>
      </header>

      <section className="panel" aria-label="Finalist Selection Bar">
        <form className="form-grid" onSubmit={(e) => void handleLoadMatrix(e)}>
          <label className="field-group">
            <span>Candidate Profile IDs (2 to 4 comma-separated UUIDs/IDs)</span>
            <input
              type="text"
              value={candidateIdsCsv}
              onChange={(e) => setCandidateIdsCsv(e.target.value)}
            />
          </label>
          <div className="button-row">
            <button
              type="submit"
              className="btn btn-primary"
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
                  {state.matrix.columns.map((col) => (
                    <th key={col.candidateProfileId}>
                      <div>
                        <strong>
                          #{col.rank} {col.displayName}
                        </strong>
                      </div>
                      <div className="muted">
                        Index H = {col.holisticDecisionIndex.toFixed(1)} · Auth{" "}
                        {(col.authenticityBadge.score * 100).toFixed(0)}% (
                        {col.authenticityBadge.tier})
                      </div>
                      <div className="badge badge-info">{col.recommendationBand}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>
                    <strong>Composite Calibration Summary</strong>
                  </td>
                  {state.matrix.columns.map((col) => (
                    <td key={col.candidateProfileId}>
                      <div>CV Match: {col.compositeCvMatchScore.toFixed(1)}%</div>
                      <div>Posterior: {col.rawPosteriorTechnicalScore.toFixed(1)}</div>
                      <div>
                        Gap Resolution:{" "}
                        {(col.epistemicGapResolutionRatio * 100).toFixed(0)}%
                      </div>
                    </td>
                  ))}
                </tr>
                {state.matrix.rows.map((row) => (
                  <tr key={row.dimension.competencyId}>
                    <td>
                      <strong>{row.dimension.competencyName}</strong>
                      <div className="muted">
                        Weight {row.dimension.normalizedWeightPercent.toFixed(0)}% · Target
                        L{row.dimension.targetDepth}
                      </div>
                    </td>
                    {state.matrix?.columns.map((col) => {
                      const cell = row.cellsByCandidateId[col.candidateProfileId];
                      if (cell === undefined) {
                        return <td key={col.candidateProfileId}>—</td>;
                      }
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
                                candidateLabel: col.displayName,
                              })
                            }
                          >
                            <div>
                              <strong>{cell.posteriorScore.toFixed(1)}</strong>{" "}
                              <span className="muted">
                                (EB {cell.empiricalBayesScore.toFixed(1)})
                              </span>
                            </div>
                            <div className="muted">
                              Depth {cell.achievedDepth.toFixed(1)} / L
                              {cell.targetDepth} · Peak L
                              {cell.peakDifficultyReached}
                            </div>
                            <div className="badge-row">
                              {cell.isDimensionLeader && (
                                <span className="badge badge-success">
                                  DIMENSION_LEADER
                                </span>
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
          <div className="section-row">
            <h2 className="section-title">
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
                <p>{activeEvidenceCell.cvEvidenceQuote}</p>
              </div>
              <div>
                <span className="eyebrow">Adaptive Probe Question</span>
                <p>{activeEvidenceCell.interviewQuestion}</p>
              </div>
              <div>
                <span className="eyebrow">Candidate Response Excerpt</span>
                <p>{activeEvidenceCell.candidateAnswerExcerpt}</p>
              </div>
              <div>
                <span className="eyebrow">Evaluator Rationale</span>
                <p>{activeEvidenceCell.evaluatorRationale}</p>
              </div>
            </div>
          ) : (
            <p className="muted">No detailed evidence loaded for this cell.</p>
          )}
        </aside>
      )}

      <section className="panel" aria-label="Recruiter Recommendation Override">
        <h2 className="section-title">Auditable Recruiter Override</h2>
        <form
          className="form-grid"
          onSubmit={(e) => void handleOverrideSubmit(e)}
        >
          <label className="field-group">
            <span>Evaluation Report ID</span>
            <input
              type="text"
              value={overrideReportId}
              onChange={(e) => setOverrideReportId(e.target.value)}
              placeholder="rep-..."
            />
          </label>
          <label className="field-group">
            <span>Override Recommendation Band</span>
            <select
              value={overrideBand}
              onChange={(e) =>
                setOverrideBand(e.target.value as RecommendationBand)
              }
            >
              {RECOMMENDATION_OPTIONS.map((band) => (
                <option key={band} value={band}>
                  {band}
                </option>
              ))}
            </select>
          </label>
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
              disabled={
                overrideReportId.trim().length === 0 ||
                overrideJustification.trim().length === 0 ||
                state.isLoading
              }
            >
              Record Immutable Override
            </button>
          </div>
        </form>
        {state.lastOverrideReceipt !== null && (
          <p className="status-banner success">
            Saved report <strong>{state.lastOverrideReceipt.report_id}</strong>{" "}
            at band <strong>{state.lastOverrideReceipt.effective_recommendation_band}</strong>.
          </p>
        )}
      </section>
    </div>
  );
}
