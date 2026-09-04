import React from "react";
import { PipelineState } from "../../types/pipeline";

export const GlobalRankView: React.FC<{ state: PipelineState }> = ({ state }) => {
  const topOpts = state.top_options;

  return (
    <div className="workspace-card">
      <div className="workspace-title-section">
        <h2 className="workspace-title">6. Option Global LLM Ranker</h2>
        <div className="workspace-description">
          Cross-underlying option comparison prioritizing the most efficient contracts across all stocks based on thesis fit, premium paid, and Greek leverage.
        </div>
      </div>

      {topOpts.length === 0 ? (
        <div style={{ color: "var(--md-sys-color-on-surface-variant)", padding: "24px 0" }}>
          Awaiting cross-asset option global ranking...
        </div>
      ) : (
        <div className="m3-table-wrapper">
          <table className="m3-table">
            <thead>
              <tr>
                <th style={{ width: "85px" }}>Global Rank</th>
                <th>Option Contract</th>
                <th>Type</th>
                <th style={{ textAlign: "right" }}>Strike</th>
                <th style={{ textAlign: "right" }}>DTE</th>
                <th style={{ textAlign: "right" }}>Mid Price</th>
                <th style={{ textAlign: "right" }}>Spread</th>
                <th style={{ textAlign: "right" }}>Delta</th>
                <th style={{ textAlign: "right" }}>Global Score</th>
              </tr>
            </thead>
            <tbody>
              {topOpts.map((opt, idx) => (
                <tr key={`${opt.symbol}-${opt.rank}-${idx}`}>
                  <td className="tabular-nums" style={{ fontWeight: 700, fontSize: "14px" }}>
                    #{opt.rank}
                  </td>
                  <td style={{ fontWeight: 700, color: "var(--md-sys-color-primary)", fontSize: "13px" }}>
                    {opt.symbol}
                  </td>
                  <td>
                    <span className="m3-badge success" style={{ padding: "1px 6px", fontSize: "11px", textTransform: "uppercase" }}>
                      {opt.option_type}
                    </span>
                  </td>
                  <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                    ${opt.strike.toFixed(2)}
                  </td>
                  <td className="tabular-nums" style={{ textAlign: "right" }}>
                    {opt.dte}d
                  </td>
                  <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                    ${opt.mid.toFixed(2)}
                  </td>
                  <td className="tabular-nums" style={{ textAlign: "right" }}>
                    {opt.spread_pct}
                  </td>
                  <td className="tabular-nums positive" style={{ textAlign: "right", fontWeight: 600 }}>
                    +{opt.delta.toFixed(3)}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <span className="m3-badge success tabular-nums" style={{ fontWeight: 600 }}>
                      {opt.score.toFixed(1)}
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
