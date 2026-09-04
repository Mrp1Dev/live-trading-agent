import React from "react";
import { PipelineState } from "../../types/pipeline";

export const OptionsChainView: React.FC<{ state: PipelineState }> = ({ state }) => {
  const options = state.option_candidates;
  const skippedEntries = Object.entries(state.skipped_symbols);

  return (
    <div className="workspace-card">
      <div className="workspace-title-section">
        <h2 className="workspace-title">5. Quantitative BSM Option Chain Selector</h2>
        <div className="workspace-description">
          Deterministic Black-Scholes-Merton model filtering contracts for delta efficiency, IV responsiveness, spread tightness, and catalyst timing.
        </div>
      </div>

      {/* Skipped Underlyings Notice */}
      {skippedEntries.length > 0 && (
        <div
          style={{
            padding: "12px 16px",
            borderRadius: "var(--md-shape-md)",
            backgroundColor: "var(--md-sys-color-surface-container)",
            border: "1px solid var(--md-sys-color-outline-variant)",
            marginBottom: "16px",
            fontSize: "13px",
            display: "flex",
            flexDirection: "column",
            gap: "6px",
          }}
        >
          <div style={{ fontWeight: 600, color: "var(--md-sys-color-on-surface)" }}>
            Deterministic Filter Results ({skippedEntries.length} underlyings eliminated):
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
            {skippedEntries.map(([sym, reason]) => (
              <span
                key={sym}
                className="m3-badge"
                style={{
                  backgroundColor: "var(--md-sys-color-surface-container-highest)",
                  color: "var(--md-sys-color-on-surface-variant)",
                  padding: "3px 8px",
                  fontSize: "11px",
                }}
                title={reason}
              >
                <strong>{sym}:</strong> {reason}
              </span>
            ))}
          </div>
        </div>
      )}

      {options.length === 0 ? (
        <div
          style={{
            color: "var(--md-sys-color-on-surface-variant)",
            padding: "32px 16px",
            textAlign: "center",
            backgroundColor: "var(--md-sys-color-surface-container-low)",
            borderRadius: "var(--md-shape-lg)",
            border: "1px dashed var(--md-sys-color-outline-variant)",
          }}
        >
          <div style={{ fontWeight: 600, fontSize: "14px", marginBottom: "6px", color: "var(--md-sys-color-on-surface)" }}>
            ⚡ Option Chain Retrieval & Greek Filtering Active
          </div>
          <div style={{ fontSize: "12px" }}>
            Evaluating historical contracts via Alpaca Options Data Client. Applying IV, Delta, Gamma, Theta and spread gates.
          </div>
        </div>
      ) : (
        <div className="m3-table-wrapper">
          <table className="m3-table">
            <thead>
              <tr>
                <th style={{ width: "65px" }}>ID</th>
                <th>Underlying</th>
                <th>Contract</th>
                <th>Type</th>
                <th style={{ textAlign: "right" }}>Strike</th>
                <th style={{ textAlign: "right" }}>DTE</th>
                <th style={{ textAlign: "right" }}>Moneyness</th>
                <th style={{ textAlign: "right" }}>Ask Price</th>
                <th style={{ textAlign: "right" }}>Spread</th>
                <th style={{ textAlign: "right" }}>IV</th>
                <th style={{ textAlign: "right" }}>Delta</th>
                <th style={{ textAlign: "right" }}>Theta</th>
                <th style={{ textAlign: "right" }}>Selector Score</th>
              </tr>
            </thead>
            <tbody>
              {options.map((opt, idx) => (
                <tr key={`${opt.id}-${opt.symbol}-${idx}`}>
                  <td style={{ fontWeight: 600, color: "var(--md-sys-color-on-surface-variant)" }}>
                    {opt.id}
                  </td>
                  <td style={{ fontWeight: 700, color: "var(--md-sys-color-primary)", fontSize: "14px" }}>
                    {opt.stock}
                  </td>
                  <td style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)" }}>
                    {opt.symbol}
                  </td>
                  <td style={{ textTransform: "uppercase", fontWeight: 600 }}>
                    <span
                      className={`m3-badge ${opt.option_type === "call" ? "success" : "warning"}`}
                      style={{ padding: "1px 6px", fontSize: "11px" }}
                    >
                      {opt.option_type}
                    </span>
                  </td>
                  <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                    ${opt.strike.toFixed(2)}
                  </td>
                  <td className="tabular-nums" style={{ textAlign: "right" }}>
                    {opt.dte}d
                  </td>
                  <td className="tabular-nums" style={{ textAlign: "right" }}>
                    {opt.moneyness}
                  </td>
                  <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                    ${opt.ask.toFixed(2)}
                  </td>
                  <td className="tabular-nums" style={{ textAlign: "right" }}>
                    {opt.spread_pct}
                  </td>
                  <td className="tabular-nums" style={{ textAlign: "right" }}>
                    {opt.iv}
                  </td>
                  <td className="tabular-nums positive" style={{ textAlign: "right", fontWeight: 600 }}>
                    +{opt.delta.toFixed(3)}
                  </td>
                  <td className="tabular-nums negative" style={{ textAlign: "right" }}>
                    {opt.theta.toFixed(3)}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <span className="m3-badge success tabular-nums" style={{ fontWeight: 600 }}>
                      {opt.selector_score.toFixed(1)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
