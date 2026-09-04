import React, { useEffect, useState } from "react";
import { PipelineState } from "../../types/pipeline";

export const ResearchView: React.FC<{ state: PipelineState }> = ({ state }) => {
  const reports = Object.values(state.research_reports);
  const [pinnedSymbol, setPinnedSymbol] = useState<string | null>(null);
  const [revealedCount, setRevealedCount] = useState<number>(1);

  // Progressive one-by-one reveal of LLM research results
  useEffect(() => {
    if (reports.length === 0) {
      setRevealedCount(1);
      return;
    }

    if (revealedCount < reports.length) {
      const timer = setTimeout(() => {
        setRevealedCount((prev) => Math.min(prev + 1, reports.length));
      }, 650);
      return () => clearTimeout(timer);
    }
  }, [revealedCount, reports.length]);

  const visibleReports = reports.slice(0, Math.max(1, revealedCount));
  const activeSymbol = pinnedSymbol || (visibleReports.length > 0 ? visibleReports[visibleReports.length - 1].symbol : "");
  const activeReport = visibleReports.find((r) => r.symbol === activeSymbol) || visibleReports[0];

  return (
    <div className="workspace-card">
      <div className="workspace-title-section">
        <h2 className="workspace-title">3. DeepSeek Qualitative Research Agent</h2>
        <div className="workspace-description">
          Real-time news summarization, earnings catalyst identification, and risk detection synthesized one-by-one across scanned stocks.
        </div>
      </div>

      {reports.length === 0 ? (
        <div style={{ color: "var(--md-sys-color-on-surface-variant)", padding: "24px 0" }}>
          Awaiting research dossiers from the DeepSeek qualitative agent...
        </div>
      ) : (
        <div>
          {/* Real-time Research Progress Header */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "10px 16px",
              backgroundColor: "var(--md-sys-color-surface-container)",
              borderRadius: "var(--md-shape-md)",
              marginBottom: "16px",
              border: "1px solid var(--md-sys-color-outline-variant)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <span className="pulse-dot" />
              <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--md-sys-color-on-surface)" }}>
                {revealedCount < reports.length
                  ? `✨ AI Research Synthesizing: Evaluating ${revealedCount} of ${reports.length} Stocks...`
                  : `✓ All ${reports.length} Candidate Research Dossiers Evaluated`}
              </span>
            </div>

            {revealedCount < reports.length && (
              <button
                type="button"
                className="btn-m3-tonal"
                style={{ height: "28px", padding: "0 12px", fontSize: "11px" }}
                onClick={() => setRevealedCount(reports.length)}
              >
                ⚡ Reveal All
              </button>
            )}
          </div>

          {/* Symbol Selector Chips */}
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "8px",
              alignItems: "center",
              marginBottom: "20px",
            }}
          >
            {visibleReports.map((r, idx) => {
              const isSelected = activeReport?.symbol === r.symbol;
              return (
                <button
                  key={`${r.symbol}-${idx}`}
                  type="button"
                  className={`btn-m3-tonal ${isSelected ? "active" : ""}`}
                  style={{
                    height: "34px",
                    padding: "0 14px",
                    fontSize: "12px",
                    fontWeight: isSelected ? 700 : 500,
                    width: "auto",
                    maxWidth: "max-content",
                    flex: "0 0 auto",
                    backgroundColor: isSelected ? "var(--md-sys-color-primary)" : "var(--md-sys-color-surface-container-high)",
                    color: isSelected ? "var(--md-sys-color-on-primary)" : "var(--md-sys-color-on-surface)",
                    boxShadow: isSelected ? "var(--md-elevation-1)" : "none",
                    animation: "fadeIn 0.3s ease-in-out",
                  }}
                  onClick={() => setPinnedSymbol(r.symbol)}
                  title={`View news dossier for ${r.symbol}`}
                >
                  <span>{r.symbol}</span>
                  {r.timing === "IMMEDIATE" && (
                    <span
                      style={{
                        fontSize: "10px",
                        padding: "1px 5px",
                        borderRadius: "9999px",
                        backgroundColor: isSelected ? "rgba(255,255,255,0.28)" : "var(--md-sys-color-warning-container)",
                        color: isSelected ? "#fff" : "var(--md-sys-color-on-warning-container)",
                        marginLeft: "6px",
                      }}
                    >
                      NOW
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {activeReport && (
            <div className="dossier-card">
              <div className="dossier-header">
                <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
                  <span className="dossier-symbol">{activeReport.symbol}</span>
                  <span className="m3-badge neutral" style={{ fontSize: "11px" }}>
                    {activeReport.articles_count} article{activeReport.articles_count === 1 ? "" : "s"} analyzed
                  </span>
                  {pinnedSymbol && (
                    <button
                      type="button"
                      className="btn-m3-outlined"
                      style={{ height: "26px", padding: "0 8px", fontSize: "11px" }}
                      onClick={() => setPinnedSymbol(null)}
                      title="Follow latest analyzed symbol"
                    >
                      ↺ Auto-Follow Latest
                    </button>
                  )}
                </div>
                <span className={`m3-badge ${activeReport.timing === "IMMEDIATE" ? "warning" : "neutral"}`}>
                  Timing: {activeReport.timing}
                </span>
              </div>

              {/* Quant Summary */}
              <div style={{ marginBottom: "16px" }}>
                <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "var(--md-sys-color-on-surface-variant)", marginBottom: "4px" }}>
                  Deterministic Technical Profile
                </div>
                <div style={{ fontSize: "13px", lineHeight: 1.5, color: "var(--md-sys-color-on-surface)" }}>
                  {activeReport.quant_summary || "Calculated quant momentum profile."}
                </div>
              </div>

              {/* News Narrative */}
              <div style={{ marginBottom: "16px" }}>
                <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "var(--md-sys-color-on-surface-variant)", marginBottom: "4px" }}>
                  Synthesized News & Market Context
                </div>
                <div style={{ fontSize: "13px", lineHeight: 1.6, color: "var(--md-sys-color-on-surface)" }}>
                  {activeReport.news_summary || "No material news reported for this symbol."}
                </div>
              </div>

              {/* Catalyst & Risk Grid */}
              <div className="dossier-grid" style={{ marginBottom: "16px" }}>
                <div className="dossier-box">
                  <div style={{ fontWeight: 600, fontSize: "12px", color: "var(--md-sys-color-success)", marginBottom: "4px" }}>
                    🎯 Directional Catalyst
                  </div>
                  <div style={{ fontSize: "12px", lineHeight: 1.5 }}>
                    {activeReport.catalyst || "None reported."}
                  </div>
                </div>

                <div className="dossier-box">
                  <div style={{ fontWeight: 600, fontSize: "12px", color: "var(--md-sys-color-error)", marginBottom: "4px" }}>
                    ⚠️ Potential Risk Factor
                  </div>
                  <div style={{ fontSize: "12px", lineHeight: 1.5 }}>
                    {activeReport.risk || "None reported."}
                  </div>
                </div>
              </div>

              {/* Source Articles */}
              {activeReport.sources && activeReport.sources.length > 0 && (
                <div>
                  <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "var(--md-sys-color-on-surface-variant)", marginBottom: "6px" }}>
                    Attributed Sources ({activeReport.sources.length})
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                    {activeReport.sources.map((src, i) => (
                      <div
                        key={i}
                        style={{
                          fontSize: "12px",
                          color: "var(--md-sys-color-on-surface-variant)",
                          backgroundColor: "var(--md-sys-color-surface-container-low)",
                          padding: "6px 12px",
                          borderRadius: "var(--md-shape-sm)",
                          border: "1px solid var(--md-sys-color-outline-variant)",
                        }}
                      >
                        📄 {src}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
